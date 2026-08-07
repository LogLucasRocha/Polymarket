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

**Conta por e-mail (Magic):** o Polymarket usa uma proxy wallet. A chave é o
SIGNER (o "Signer Address" da conta); os fundos ficam no FUNDER (o "Polymarket
Wallet Address"). Configure também:

```powershell
$env:POLYMARKET_FUNDER = "0xSEU_POLYMARKET_WALLET_ADDRESS"
# se necessário: $env:POLYMARKET_SIGNATURE_TYPE = "1"   # 1=e-mail, 2=navegador
```

Testes progressivos (cada um mais fundo que o anterior):

```powershell
python run_executor.py check   # 1) offline: confere o signer
python run_executor.py auth    # 2) conecta ao CLOB (não envia ordem)
python run_executor.py         # 3) dry-run: mostra o que compraria
```

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

## Rodar local (recomendado — na sua região)

⚠️ **A nuvem (GitHub Actions) NÃO executa ordens:** os runners ficam nos EUA e
o Polymarket bloqueia trading de lá (HTTP 403 "Trading restricted in your
region"). Ordens reais só saem de onde você tem permissão — a **sua máquina**.

Um wrapper roda, a cada ~5 min, dois comandos em sequência: o `send_telegram`
em modo `--signals-only` (calcula e grava os sinais, **sem** mandar Telegram) e
o `run_executor`. Crie `rodar_executor_local.ps1` na pasta do projeto:

```powershell
Set-Location $PSScriptRoot
# --- endereços (públicos) e limites ---
$env:POLYMARKET_FUNDER = "0xSEU_POLYMARKET_WALLET_ADDRESS"
$env:POLYMARKET_WALLET = "0xSEU_POLYMARKET_WALLET_ADDRESS"
$env:POLYMARKET_SIGNATURE_TYPE = "1"
$env:CEIFA_EXEC_ENABLED = "true"
$env:CEIFA_EXEC_DRY_RUN = "true"      # comece simulando!
$env:CEIFA_EXEC_MAX_STAKE_USD = "2"
$env:CEIFA_EXEC_MAX_EXPOSURE_USD = "20"
# A CHAVE não fica aqui: defina uma vez, persistente, com
#   setx POLYMARKET_PRIVATE_KEY "0xSUACHAVE"
# (reabra o PowerShell depois do setx para ele valer)
python send_telegram.py --signals-only
python run_executor.py
```

Rode manualmente primeiro (em dry-run) e confira os `[executor] DRY-RUN ...`.
Quando confiar, mude `CEIFA_EXEC_DRY_RUN` para `"false"`.

**Agendar a cada 5 min** (Windows): Agendador de Tarefas → Criar Tarefa →
Disparador "repetir a cada 5 minutos" → Ação: `powershell.exe -NoProfile
-ExecutionPolicy Bypass -File "C:\...\rodar_executor_local.ps1"`. O PC precisa
estar ligado e na sua rede/região habitual do Polymarket.

**Liga/desliga local:** mude `CEIFA_EXEC_ENABLED`/`CEIFA_EXEC_DRY_RUN` no
wrapper, ou crie o arquivo `STOP_EXECUTOR` na pasta para parar na hora.

## Rodar na nuvem (GitHub Actions) — ⚠️ não executa (geo-bloqueio)

> Mantido só como referência. Os runners do GitHub ficam nos EUA e o Polymarket
> recusa as ordens com 403. Deixe `CEIFA_EXEC_ENABLED=false` no repo.

O workflow `main.yml` já roda o alerta a cada 10 min e, logo depois, o passo
**"Executar ordens da Ceifa"**. Por padrão ele fica **desligado e em dry-run**
— nada executa até você configurar, em **Settings → Secrets and variables →
Actions**:

| Tipo | Nome | Valor |
|---|---|---|
| **Secret** | `POLYMARKET_PRIVATE_KEY` | a chave (⚠️ use uma **carteira dedicada**, com só a banca) |
| Variable | `POLYMARKET_FUNDER` | o "Polymarket Wallet Address" |
| Variable | `POLYMARKET_SIGNATURE_TYPE` | `1` (e-mail) ou `2` (navegador) — opcional |
| Variable | `CEIFA_EXEC_ENABLED` | `true` para ligar |
| Variable | `CEIFA_EXEC_DRY_RUN` | `true` = simula · `false` = **real** |
| Variable | `CEIFA_EXEC_MAX_STAKE_USD` | teto por ordem (padrão 2) |
| Variable | `CEIFA_EXEC_MAX_EXPOSURE_USD` | teto por dia (padrão 20) |

**Ligar/desligar na nuvem:**
- **Observar (dry-run):** `CEIFA_EXEC_ENABLED=true`, `CEIFA_EXEC_DRY_RUN=true`.
  Veja nos logs do Actions o que ele *compraria*.
- **Operar de verdade:** `CEIFA_EXEC_DRY_RUN=false`.
- **Kill switch:** zere ou apague `CEIFA_EXEC_ENABLED` — o passo volta a
  recusar tudo na próxima rodada.

> ⚠️ A chave fica como secret na nuvem e roda sem supervisão. **Use uma
> carteira separada, só com a banca que você toparia perder.** O teto de
> exposição limita o gasto por dia, mas não protege contra roubo da chave —
> só uma carteira de saldo baixo protege.

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
