"""Executor automático das ordens da Ceifa na Polymarket (API CLOB).

Substitui um "autoclicker" de tela por execução programática, que conhece o
preço e o tamanho exatos e RECUSA a ordem quando algo não bate.

SEGURANÇA EM CAMADAS (todas ligadas por padrão):
  1. ``CEIFA_EXEC_ENABLED`` começa False — nada é executado.
  2. Mesmo ligado, ``CEIFA_EXEC_DRY_RUN`` começa True — só registra o que faria.
  3. Um arquivo "kill switch" (``CEIFA_EXEC_KILL_FILE``) aborta tudo se existir.
  4. Teto por ordem e teto de exposição total no dia.
  5. Preço obrigatoriamente dentro da faixa da Ceifa (0,95–0,995).
  6. Idempotência: cada ``ref`` só é executado uma vez (ledger em disco).

A CHAVE DA CARTEIRA nunca fica no código nem em log: vem de
``POLYMARKET_PRIVATE_KEY``. Os controles de runtime (ligar, dry-run, limites)
podem ser sobrescritos por variável de ambiente de mesmo nome, para você mudar
sem editar o código.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from tmax import config

CLOB_HOST = "https://clob.polymarket.com"


class ExecutorError(RuntimeError):
    """Falha dura (configuração/credencial/lib ausente)."""


class OrderRejected(ExecutorError):
    """A ordem foi barrada por uma trava de segurança — comportamento esperado."""


# ------------------------------------------------------------- leitura de ambiente
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "sim")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def settings() -> dict:
    """Configuração efetiva do executor (config + overrides de ambiente)."""
    return {
        "enabled": _env_bool("CEIFA_EXEC_ENABLED", config.CEIFA_EXEC_ENABLED),
        "dry_run": _env_bool("CEIFA_EXEC_DRY_RUN", config.CEIFA_EXEC_DRY_RUN),
        "max_stake": _env_float(
            "CEIFA_EXEC_MAX_STAKE_USD", config.CEIFA_EXEC_MAX_STAKE_USD),
        "max_exposure": _env_float(
            "CEIFA_EXEC_MAX_EXPOSURE_USD", config.CEIFA_EXEC_MAX_EXPOSURE_USD),
        "price_min": config.CEIFA_PRICE_MIN,
        "price_max": config.CEIFA_PRICE_MAX,
        "kill_file": _env_str("CEIFA_EXEC_KILL_FILE", config.CEIFA_EXEC_KILL_FILE),
        "ledger": _env_str("CEIFA_EXEC_LEDGER", config.CEIFA_EXEC_LEDGER),
        "chain_id": int(config.CEIFA_EXEC_CHAIN_ID),
    }


# ------------------------------------------------------------- kill switch + ledger
def kill_switch_active(cfg: dict | None = None) -> bool:
    cfg = cfg or settings()
    return (config.ROOT / cfg["kill_file"]).exists()


def _ledger_path(cfg: dict) -> Path:
    return config.ROOT / cfg["ledger"]


def load_ledger(cfg: dict | None = None) -> dict:
    cfg = cfg or settings()
    path = _ledger_path(cfg)
    if not path.exists():
        return {"orders": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"orders": {}}
    data.setdefault("orders", {})
    return data


def _save_ledger(cfg: dict, ledger: dict) -> None:
    path = _ledger_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")


def day_exposure(ledger: dict, day: str) -> float:
    """Soma dos stakes já EXECUTADOS (não dry-run) no dia."""
    return round(sum(
        float(order.get("size_usd") or 0.0)
        for order in ledger.get("orders", {}).values()
        if order.get("day") == day and order.get("status") == "placed"
    ), 6)


# ------------------------------------------------------------- cliente CLOB (isolado)
def build_client(cfg: dict | None = None):
    """Instancia o cliente py-clob-client com a chave do ambiente.

    Isolado de propósito: é a única parte que depende da versão instalada da
    ``py-clob-client``. Se a assinatura mudar, ajuste só aqui.
    """
    cfg = cfg or settings()
    key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not key:
        raise ExecutorError(
            "POLYMARKET_PRIVATE_KEY não definido — sem chave não dá para assinar "
            "ordens. Defina no ambiente da SUA máquina (nunca no código).")
    try:
        from py_clob_client.client import ClobClient  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depende do ambiente do usuário
        raise ExecutorError(
            "py-clob-client não instalado. Rode: pip install py-clob-client"
        ) from exc
    # Conta por e-mail/Magic do Polymarket usa uma proxy wallet: a chave é o
    # SIGNER (EOA) e os fundos ficam no FUNDER (o "Polymarket Wallet Address").
    # Nesse caso é preciso passar funder + signature_type (1 = e-mail/Magic;
    # 2 = carteira de navegador). Sem funder, assume conta EOA simples.
    funder = os.environ.get("POLYMARKET_FUNDER") or None
    sig_env = os.environ.get("POLYMARKET_SIGNATURE_TYPE")
    kwargs = {"chain_id": cfg["chain_id"]}
    if funder:
        kwargs["funder"] = funder
        kwargs["signature_type"] = int(sig_env) if sig_env is not None else 1
    elif sig_env is not None:
        kwargs["signature_type"] = int(sig_env)
    client = ClobClient(CLOB_HOST, key=key, **kwargs)
    # Deriva as credenciais de API a partir da chave (L2 headers).
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def wallet_address() -> str:
    """Deriva o endereço público da chave em POLYMARKET_PRIVATE_KEY, OFFLINE.

    Não toca a rede nem envia ordem — só confirma que a chave corresponde à
    carteira que você espera. Compare o endereço impresso com o da sua conta
    na Polymarket antes de ligar qualquer execução.
    """
    key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not key:
        raise ExecutorError(
            "POLYMARKET_PRIVATE_KEY não definido no ambiente.")
    try:
        from eth_account import Account  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depende do ambiente do usuário
        raise ExecutorError(
            "eth-account não instalado (vem junto com py-clob-client)."
        ) from exc
    return Account.from_key(key).address


def _submit_order(client, token_id: str, price: float, size_shares: float):
    """Envia uma ordem de COMPRA do token (NÃO) ao CLOB. Retorna a resposta crua.

    Verifique os nomes contra a sua versão da py-clob-client se necessário.
    """
    from py_clob_client.clob_types import OrderArgs, OrderType  # noqa: PLC0415
    from py_clob_client.order_builder.constants import BUY  # noqa: PLC0415

    order = client.create_order(OrderArgs(
        token_id=str(token_id),
        price=round(float(price), 3),
        size=round(float(size_shares), 2),
        side=BUY,
    ))
    return client.post_order(order, OrderType.GTC)


# ------------------------------------------------------------- execução com travas
def place_no_order(token_id: str, price: float, size_usd: float, *,
                   ref: str, day: str, client=None,
                   cfg: dict | None = None, now: dt.datetime | None = None) -> dict:
    """Executa (ou simula) a compra do NÃO de um contrato, com todas as travas.

    ``ref`` identifica unicamente a parcela (ex.: ``ICAO:dia:faixa:HHMM``) — o
    mesmo ``ref`` nunca é executado duas vezes. Retorna um dict com ``status``:
    ``rejected`` (barrado por trava), ``dry_run`` (só registrou) ou ``placed``.
    Nunca levanta em rejeição de trava — isso é caminho normal e seguro.
    """
    cfg = cfg or settings()
    now = now or dt.datetime.now(dt.timezone.utc)
    result = {"ref": ref, "day": day, "token_id": str(token_id),
              "price": round(float(price), 6), "size_usd": round(float(size_usd), 6),
              "ts": now.isoformat()}

    def reject(reason: str) -> dict:
        result.update(status="rejected", reason=reason)
        print(f"[executor] RECUSADO {ref}: {reason}")
        return result

    if not cfg["enabled"]:
        return reject("executor desligado (CEIFA_EXEC_ENABLED=False)")
    if kill_switch_active(cfg):
        return reject(f"kill switch ativo (arquivo {cfg['kill_file']} existe)")
    price = round(float(price), 6)
    if not (cfg["price_min"] < price < cfg["price_max"]):
        return reject(
            f"preço {price:.3f} fora da faixa "
            f"({cfg['price_min']}–{cfg['price_max']})")
    size_usd = round(float(size_usd), 6)
    if size_usd <= 0:
        return reject("stake ≤ 0")
    if size_usd > cfg["max_stake"] + 1e-9:
        return reject(
            f"stake ${size_usd:.2f} acima do teto por ordem "
            f"(${cfg['max_stake']:.2f})")

    ledger = load_ledger(cfg)
    if ref in ledger["orders"] and \
            ledger["orders"][ref].get("status") in ("placed", "dry_run"):
        return reject(f"ref já processado ({ledger['orders'][ref].get('status')})")
    exposure = day_exposure(ledger, day)
    if exposure + size_usd > cfg["max_exposure"] + 1e-9:
        return reject(
            f"exposição do dia ${exposure:.2f}+${size_usd:.2f} passaria do teto "
            f"(${cfg['max_exposure']:.2f})")

    if cfg["dry_run"]:
        result.update(status="dry_run", reason="dry-run (nenhuma ordem enviada)")
        ledger["orders"][ref] = result
        _save_ledger(cfg, ledger)
        print(f"[executor] DRY-RUN {ref}: compraria NÃO @ {price:.3f} "
              f"(${size_usd:.2f})")
        return result

    client = client or build_client(cfg)
    size_shares = size_usd / price
    try:
        response = _submit_order(client, token_id, price, size_shares)
    except OrderRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — falha de envio não pode escalar cega
        result.update(status="error", reason=f"falha no envio: {exc}")
        ledger["orders"][ref] = result
        _save_ledger(cfg, ledger)
        print(f"[executor] ERRO ao enviar {ref}: {exc}")
        return result

    result.update(status="placed", size_shares=round(size_shares, 4),
                  response=_summarize_response(response))
    ledger["orders"][ref] = result
    _save_ledger(cfg, ledger)
    print(f"[executor] ENVIADA {ref}: NÃO @ {price:.3f} "
          f"({size_shares:.2f} cotas, ${size_usd:.2f})")
    return result


def _summarize_response(response) -> dict:
    """Guarda só o essencial da resposta do CLOB no ledger (sem dados sensíveis)."""
    if isinstance(response, dict):
        return {k: response.get(k) for k in ("orderID", "orderId", "success",
                                             "status", "errorMsg")
                if k in response}
    return {"raw": str(response)[:200]}


def execute_signals(signals: list[dict], cfg: dict | None = None) -> list[dict]:
    """Executa uma lista de ordens da Ceifa, uma a uma, com todas as travas.

    Cada ``signal`` precisa de: ``token_id`` (do NÃO), ``price``, ``size_usd``,
    ``ref`` (id único da parcela) e ``day``. Reaproveita um único cliente CLOB
    quando há ordem real a enviar. Retorna a lista de resultados.
    """
    cfg = cfg or settings()
    results: list[dict] = []
    client = None
    needs_client = cfg["enabled"] and not cfg["dry_run"] and not kill_switch_active(cfg)
    if needs_client and signals:
        client = build_client(cfg)
    for signal in signals:
        results.append(place_no_order(
            signal["token_id"], signal["price"], signal["size_usd"],
            ref=signal["ref"], day=signal["day"], client=client, cfg=cfg))
    placed = sum(1 for r in results if r["status"] == "placed")
    dry = sum(1 for r in results if r["status"] == "dry_run")
    rejected = sum(1 for r in results if r["status"] == "rejected")
    print(f"[executor] resumo: {placed} enviadas · {dry} dry-run · "
          f"{rejected} recusadas · de {len(results)} sinais.")
    return results
