"""Cura o buffer intradiário a partir do release ceifa-live (durabilidade).

O buffer intradiário (``data/capture/buffer``, ``data_low``, ``data_spy``) vive
só no cache efêmero do GitHub Actions. Se o cache reseta antes das 00 UTC,
perde-se o intradiário que ainda não foi consolidado em ``dados/`` — foi o que
aconteceu em 07/08, quando a manhã inteira sumiu.

Rodado no INÍCIO de cada rodada (antes de capturar), este script baixa o último
release e faz a UNIÃO das linhas com o buffer local. Como o release é
reconstruído a partir do buffer no fim de cada rodada, um reset de cache passa a
se auto-corrigir a partir do último snapshot publicado — sem git churn e sem
remover nada. União exata de linhas: nunca perde, nunca duplica.

Uso: python heal_buffer.py
"""
from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import io
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import requests

from spy import MERCADOS
from tmax import config

RELEASE_API = ("https://api.github.com/repos/LogLucasRocha/Polymarket/"
               "releases/tags/ceifa-live")
# Prefixos de buffer que o release carrega (os mesmos do passo do zip no
# main.yml). Só curamos esses caminhos, nunca algo fora do buffer. Os mercados
# binários (data_spy, data_bitcoin, ...) vêm do registro.
BUFFER_PREFIXES = ("data/capture/buffer/", "data_low/",
                   *(f"data_{key}/" for key in MERCADOS))


def _merge_lines(dest: Path, incoming: list[str]) -> int:
    """União das linhas do release com as locais; devolve quantas foram novas."""
    existing = (dest.read_text(encoding="utf-8").splitlines()
                if dest.exists() else [])
    existing_set = {line.strip() for line in existing if line.strip()}
    seen: set[str] = set()
    out: list[str] = []
    for line in [*incoming, *existing]:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            out.append(line)
    added = sum(1 for line in incoming
                if line.strip() and line.strip() not in existing_set)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    return added


# Marcador de que o release foi CONFIRMADO nesta rodada (existe e foi lido, ou
# ainda não existe). Só então é seguro sobrescrever o release com o buffer local
# — senão o --clobber pode apagar dado que a cache resetada não tem. O passo de
# publicação no main.yml só sobrescreve o release se este marcador existir.
OK_MARKER = config.ROOT / ".heal_ok"


def heal() -> bool:
    """Restaura o buffer do release. Devolve True se é SEGURO republicar
    (release lido com sucesso, ou ainda não existe); False se o release existe
    mas não deu para confirmar — aí NÃO se deve sobrescrever (preserva o dado)."""
    headers = {"User-Agent": config.USER_AGENT,
               "Accept": "application/vnd.github+json"}
    try:
        release = requests.get(RELEASE_API, headers=headers, timeout=30)
        if release.status_code == 404:
            print("[heal] release ainda não publicado; nada a curar.")
            return True                        # não há dado a preservar
        release.raise_for_status()
        asset = next((item for item in release.json().get("assets", [])
                      if item.get("name") == "ceifa-live.zip"), None)
        if not asset:
            print("[heal] release sem asset; nada a curar.")
            return True
        blob = requests.get(asset["browser_download_url"], headers=headers,
                            timeout=60)
        blob.raise_for_status()
        total = 0
        with ZipFile(io.BytesIO(blob.content)) as archive:
            for name in archive.namelist():
                if not name.endswith(".jsonl"):
                    continue
                if not any(name.startswith(p) for p in BUFFER_PREFIXES):
                    continue
                lines = archive.read(name).decode("utf-8").splitlines()
                total += _merge_lines(config.ROOT / name, lines)
        print(f"[heal] {total} linha(s) restaurada(s) do release para o buffer.")
        return True
    except (requests.RequestException, BadZipFile, OSError) as exc:  # noqa: BLE001
        # Release pode EXISTIR mas não deu para baixar → NÃO é seguro republicar.
        print(f"[heal] falha ao confirmar o release: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    OK_MARKER.unlink(missing_ok=True)
    if heal():
        OK_MARKER.write_text("ok", encoding="utf-8")
    sys.exit(0)
