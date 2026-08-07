# Executor automático da Ceifa (API CLOB)

Executa as ordens da Ceifa direto na Polymarket pela API, no lugar de um
"autoclicker" de tela. Ele conhece preço e tamanho exatos e **recusa** a ordem
quando algo não bate — muito mais seguro que clicar às cegas.

> ⚠️ **Dinheiro real, sem humano no meio.** Comece sempre em dry-run, com
> limites baixos, e só ligue o modo real depois de conferir. Verifique também
> os termos da Polymarket sobre trading automatizado.

## Camadas de segurança (todas ligadas por padrão)

1. `CEIFA_EXEC_ENABLED=False` — desligado; nada é executado.
2. `CEIFA_EXEC_DRY_RUN=True` — mesmo ligado, só registra o que faria.
3. **Kill switch**: se o arquivo `STOP_EXECUTOR` existir na raiz, aborta tudo.
4. Teto por ordem (`CEIFA_EXEC_MAX_STAKE_USD`) e teto de exposição no dia
   (`CEIFA_EXEC_MAX_EXPOSURE_USD`).
5. Preço obrigatoriamente na faixa da Ceifa (0,95–0,995).
6. Idempotência: cada parcela (`ref`) só é executada uma vez (ledger em disco).

## Setup (na SUA máquina)

```bash
pip install py-clob-client            # não vem por padrão
```

Defina a chave da carteira **só no ambiente**, nunca no código:

```powershell
# PowerShell (Windows)
$env:POLYMARKET_PRIVATE_KEY = "0xSUACHAVE"
```

A carteira precisa estar com USDC na Polygon e habilitada para operar na
Polymarket (mesma carteira que você usa no site).

## Como rodar

```bash
python run_executor.py                 # lê data/executor_signals.json
```

Primeira vez, deixe em dry-run (padrão) e confira as linhas
`[executor] DRY-RUN ...` — é o que ele compraria. Quando estiver seguro:

```powershell
$env:CEIFA_EXEC_ENABLED = "true"       # liga
$env:CEIFA_EXEC_DRY_RUN = "false"      # modo real
$env:CEIFA_EXEC_MAX_STAKE_USD = "5"    # comece pequeno
```

Para parar na hora: crie o arquivo `STOP_EXECUTOR` na raiz do projeto (ou
apague a variável `CEIFA_EXEC_ENABLED`).

## De onde vêm os sinais

O `run_executor.py` lê uma lista de parcelas de um JSON
(`data/executor_signals.json`):

```json
[{"ref": "ZGSZ:2026-08-07:36°C:1105", "day": "2026-08-07",
  "token_id": "0x...", "price": 0.969, "size_usd": 8.45}]
```

**Fonte única de verdade:** quem decide as entradas é o `send_telegram`
(mesmos H-1, preço, livro e filtros do alerta). O passo que falta é fazer o
`send_telegram` **gravar esse JSON** (com o `token_id` do NÃO de cada faixa) a
cada rodada — aí o executor executa exatamente o que o alerta mandou, sem
recalcular nem divergir. Esse passo mexe no fluxo do alerta ao vivo, então é
feito à parte, com sua confirmação.

## O que já está pronto e testado

- `tmax/executor.py`: todas as travas + envio isolado (a única parte que
  depende da versão da `py-clob-client`).
- `run_executor.py`: lê os sinais e executa.
- `tests/test_executor.py`: cobre desligado, preço fora da faixa, teto por
  ordem, kill switch, dry-run sem enviar, idempotência e teto de exposição.
