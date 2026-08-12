# Polymarket

## Monitor Ceifa

O painel local da estratégia fica em `ceifa_monitor.py`. Ele reúne o backtest
dos snapshots, curva patrimonial, retorno diário, eficiência dos filtros e uma
autópsia visual de cada erro. A navegação (fixada no rodapé) separa duas áreas:

- **✅ Em produção** — as estratégias ativas (NÃO): consolidada, máximas e
  mínimas. Cada uma reúne, em abas, a **Visão geral**, os **Erros** e as
  **Cidades e filtros**.
- **🧪 Em teste (hipóteses)** — mercados binários diários em observação:
  **SPY**, **Bitcoin**, **Solana** e **Ethereum**, nas modalidades **Up or Down** e
  **Above** disponíveis para cada ativo.

No Windows, abra `Monitor Ceifa.cmd` ou use o atalho de mesmo nome criado na
Área de Trabalho. O iniciador sobe o Streamlit em segundo plano e abre
`http://localhost:8765` no navegador; cliques posteriores apenas reabrem o
painel já iniciado.

### Arquivo meteorológico das mínimas

A captura das temperaturas mínimas continua apenas em observação, mas guarda a
cada rodada material suficiente para uma autópsia meteorológica futura:

- `dados_low/mercado/`: preço, melhor oferta de SIM/NÃO e volume disponível;
- `dados_low/previsao/`: mediana, P10, P90, extremos do ensemble, spread frio,
  amplitude total, mínima já observada e resumo do nowcast;
- `dados_low/nowcast/`: cada observação usada no cálculo, ensemble da hora,
  desvio bruto, amortecimento e shift aplicado;
- `dados_low/metar/`: série completa de observações e METAR bruto do dia.

Também são preservadas mediana/P10/P90 brutas, antes da correção, para que a
correção de viés possa ser comparada com o ensemble original. A coleta não
promove as mínimas para apostas nem reutiliza automaticamente o filtro de
incerteza das máximas: o limite da cauda fria será calibrado separadamente.

O backtest das mínimas usa parcelas de 1% do caixa livre a cada cinco minutos
enquanto preço, oferta e H-1 continuarem elegíveis, sem teto por contrato e sem
alavancagem. Trata-se apenas de monitoramento; nenhum alerta executa uma aposta.

### Estudo de mercados binários diários (SPY, Bitcoin, Solana, Ethereum, ...)

Monitor observacional de mercados binários da Polymarket, definidos no registro
`spy.MERCADOS` (hoje **SPY**, **Bitcoin**, **Solana** e **Ethereum**; novos entram só nesse
dicionário). A cada 5 min o `spy.capture` (pendurado no `main.yml`)
arquiva, para cada mercado, um snapshot do dia com preço e melhor ask dos dois
lados em `dados_{key}/` (parquet commitado) e `data_{key}/` (buffer do dia, que
entra no zip do botão Atualizar). Fim de semana/feriado sem mercado — a rodada
apenas não grava.

O estudo (`spy.study`, aba de cada mercado em **Em teste**) aloca no lado cujo
preço estiver na faixa **(0,95, 0,998)**, adicionando 1% do caixa livre a cada
5 min — o mesmo modelo de parcelas da Ceifa. Uma parcela só é aceita quando as
melhores ofertas dos dois lados somam menos de **105¢**; em 105¢ ou mais, o
snapshot é vetado. Reporta parcelas, assertividade,
rendimento e drawdown em seis janelas relativas ao fechamento (16:00 ET): sem
janela, H-1, H-2, H-3, H-6 e H-12. Só observa; não envia alerta nem ordem.

O **SPY Up/Down** usa uma faixa mais conservadora, **(95¢, 99,5¢)**. Os demais
mercados em teste continuam em **(95¢, 99,8¢)**.

Previsão de TMax para Guarulhos, Buenos Aires e Moscou, D0 e D+1.

Pipeline que combina múltiplos modelos numéricos, ensembles, correção de viés
por estação e observações em tempo real para gerar uma **distribuição de
probabilidade** da temperatura máxima do dia — a lógica dos traders de
clima: não esperar a próxima rodada de modelo, e sim atualizar a estimativa
com o que a estação já observou.

Estações suportadas (em `tmax/config.py`):

- **SBGR** — Guarulhos (mercado de São Paulo)
- **SAEZ** — Ministro Pistarini/Ezeiza (o mercado de Buenos Aires do
  Polymarket resolve pela estação de Ezeiza via Wunderground, que é o
  METAR de SAEZ, em graus inteiros)
- **UUWW** — Moscou/Vnukovo (o mercado de Moscou resolve pela coluna
  "Temp" do weather.gov/wrh/timeseries, que é o METAR de UUWW, em °C)

## Uso

```
pip install -r requirements.txt
streamlit run ceifa_monitor.py
```

Sobe o **Monitor Ceifa** (backtest dos snapshots, curva patrimonial, retorno
diário, desempenho por cidade, eficiência dos filtros e autópsia de cada erro).
No Windows, dê dois cliques em `Monitor Ceifa.cmd`. O botão **🔄 Atualizar**
puxa o histórico consolidado e o snapshot intradiário mais recentes.

## O que o pipeline faz

1. **Observação da fonte de resolução em tempo real** — usa o METAR/SPECI do
   aviationweather.gov. A máxima já observada vira piso da distribuição.
   Shenzhen está excluída do universo operacional porque a página indicada
   pelo mercado como ZGSZ entrega observações de Lau Fau Shan, em Hong Kong.
2. **TAF** — extrai o grupo TX (máxima prevista pelo meteorologista da estação)
   e mostra como referência independente.
3. **Multi-modelo determinístico** (Open-Meteo): ECMWF IFS, GFS, ICON, GEM e
   ECMWF AIFS (IA), cada um com sua máxima corrigida de viés.
4. **Ensembles**: ECMWF ENS (51 membros) + GEFS (31 membros) → distribuição
   horária completa.
5. **Correção de viés ("MOS caseiro")**: compara as máximas previstas nos
   últimos 60 dias (API de histórico de previsões do Open-Meteo) com as máximas
   observadas na mesma fonte que resolve cada mercado (Iowa State para METAR)
   e aprende o erro sistemático de cada modelo no ponto da estação.
   Recalculado 1x/dia (cache em
   `data/bias_cache_<ICAO>.json`).
6. **Nowcast intradiário**: mede o desvio entre o observado nas últimas horas e
   o ensemble corrigido, e desloca as horas restantes de hoje por uma fração
   amortecida desse desvio (com peso menor de manhã cedo, quando nevoeiro e
   resfriamento noturno enganam).
7. **Distribuição final**: cada membro de ensemble vira uma gaussiana centrada
   na sua máxima corrigida, com desvio igual ao erro residual histórico do
   modelo (inflado 15% para D+1, encolhido em D0 conforme o dia avança). A
   mistura dá quantis, probabilidade por faixa de 1 °C e probabilidade de
   exceder cada limiar — o formato dos buckets de mercado de previsão.

## Estrutura

```
ceifa_monitor.py       Monitor Ceifa (Streamlit + Plotly)
send_telegram.py       digest para o Telegram (roda no GitHub Actions)
tmax/config.py         estações (Station), modelos, parâmetros ajustáveis
tmax/fetch.py          coleta (METAR, TAF, IEM, Open-Meteo)
tmax/bias.py           correção de viés com cache diário
tmax/distribution.py   nowcast + mistura probabilística
tmax/pipeline.py       coleta + cálculo compartilhados (contexto da previsão)
tmax/report.py         gráficos matplotlib e HTML do relatório estático
tmax/notify.py         mensagens e gráficos do Telegram
tmax/polymarket.py     posições da carteira e odds dos mercados
data/                  caches gerados em runtime (viés, estado do digest)
reports/               relatórios gerados
```

## Bot do Telegram (comandos)

O `send_telegram.py` é disparado a cada 5 min pelo cron externo; o agendamento
nativo do GitHub Actions (`main.yml`) fica a cada 10 min como redundância. Além
de mandar os alertas, agora **lê os comandos e cliques de botão** (getUpdates)
na mesma rodada — então a resposta chega com latência de **até ~5 min** (não
há servidor sempre ligado; foi a opção escolhida para não exigir infra nova).

Estratégia ativa: **Ceifa** (a única no momento — Edge pausado, Colheita
aposentada). Compra o **NÃO** na **hora local anterior ao pico previsto (H-1)**
quando `CEIFA_PRICE_MIN < preço do NÃO < CEIFA_PRICE_MAX` — só entra perto do
pico, onde o mercado quase-certo é confiável. Dois vetos de incerteza bloqueiam
a entrada: ensemble anormalmente largo; ou desvio observado bruto de pelo menos
+1,0°C OU ajuste quente do nowcast de pelo menos +1,0°C, quando a faixa vendida
fica entre mediana−0,5°C e P90+0,5°C. Se a máxima observada estiver em platô
por pelo menos duas horas, o limite inferior desce até essa máxima. A entrada
só é alertada quando existe uma oferta de venda executável do token NÃO no
livro; o texto mostra o menor ask e seu volume disponível. Na primeira rodada
rodada com uma oportunidade, o bot consulta o pUSD livre e mostra dentro da
recomendação a stake de 1% desse saldo naquele momento. O alerta repete com
intervalo mínimo de cinco minutos enquanto elegível, mesmo se já houver
posição; cada contrato indicado autoriza uma nova parcela relativa, sem teto
por contrato e sem alavancagem. A 1ª
aparição vem com um **bloco enxuto**: gráfico da
distribuição (ensemble + TAF + mediana) e texto com o **pico previsto** e a
**mediana (P10/P90)** — sem tabela de probabilidades e sem hora a hora; as
repetições vêm em texto curto. O **desempenho da Ceifa** (testes,
assertividade, rendimento, drawdown) fica no **Monitor Ceifa**
(`ceifa_monitor.py`). Você também pode pedir o relatório completo de qualquer
cidade pelo comando abaixo.

Para auditoria, cada rodada também arquiva em `dados/nowcast/` até três horas
que formaram o ajuste: METAR bruto, temperatura observada, média corrigida dos
membros, desvio horário, quantidade de membros, peso temporal, amortecimento,
desvio médio e shift final.

Comandos (também no menu “/” do Telegram):

- `/relatorio <cidade>` — relatório completo de qualquer cidade, por ICAO
  (`/relatorio SBGR`) ou nome (`/relatorio Guarulhos`, sem depender de acento
  ou maiúscula). Aliases: `/rel`, `/report`, `/cidade`.
- `/cidades` — lista as cidades monitoradas com seus ICAOs.
- `/ajuda` — como usar o bot (`/start`, `/help` também servem).

O bot só responde ao chat configurado em `TELEGRAM_CHAT_ID` (ignora qualquer
outro). O offset dos updates é guardado em `data/digest_state.json`.

## Parâmetros para calibrar (em `tmax/config.py`)

- `BIAS_LOOKBACK_DAYS` (60) — janela de aprendizado do viés
- `NOWCAST_DAMPING` (0.7) — quanto do desvio observado propagar para a tarde
- `D1_STD_INFLATION` (1.15) — inflação de incerteza para amanhã

## Como ler o relatório do backtest (Telegram, a cada 3 dias)

O workflow `backtest.yml` reconstrói a projeção hora a hora de cada dia
arquivado (`backtest_data/`), aplica a regra de sinais de produção e simula
apostar 10% do capital em cada sinal. Linha a linha do relatório:

- **"Estratégia Edge — simulação histórica · N dias-cidade · M entradas"**
  — NÃO é contagem de alertas recebidos: é quantas entradas a regra TERIA
  feito reencenando o arquivo inteiro (1 aposta por faixa/dia, no primeiro
  cruzamento de edge ≥ 5 p.p. com confiança > 90%, só NÃO com preço ≥
  NAO_MIN_PRICE, dentro da janela local `SIGNAL_HOURS`).
- **"Acerto: X%"** — fração das apostas que resolveram a favor.
- **"modelo médio Y%"** — confiança média que o modelo DECLAROU no lado
  comprado. Compare com o acerto: se declara 99% e acerta 77%, o modelo é
  superconfiante; se os dois batem, está calibrado.
- **"preço médio 0.NN"** — quanto custou, em média, cada $1 de retorno
  potencial (0.74 = pagou 74 centavos por algo que paga $1 se acertar).
- **"P&L flat: +X.XXx"** — lucro somado apostando SEMPRE 10% do capital
  INICIAL (sem reinvestir). Métrica estável para comparar regras entre si.
- **"composto: X.XXx"** — o "quanto eu teria": reinvestindo (10% do capital
  corrente em cada aposta), o capital final em múltiplos do inicial.
- **"drawdown máx X%"** — a pior queda do pico ao vale da curva composta.
  É o quanto você precisaria aguentar ver sumir sem abandonar a regra.
- **"N faixas com resolução divergente"** — mercados que resolveram
  diferente do METAR arredondado (risco da fonte de resolução oficial).
- **"Por cidade / Por lado"** — nº de apostas (acerto, P&L flat de cada
  grupo). Lado NÃO = comprar o Não; SIM = comprar o Yes.
- **"📏 Confiança ≥ 90% no D0"** — de todas as faixas em que o modelo
  declarou ≥ 90% (mesmo sem sinal), quantas ele acertou, por cidade —
  o termômetro de calibração mais direto ("declarado" vs real).
- **"🎯 Calibração (Brier antes→depois)"** — erro quadrático médio das
  probabilidades (menor = melhor) antes e depois da curva de calibração,
  por período do dia; "blend" é o diagnóstico modelo+preço (quanto menor o
  peso `a` do modelo, menos ele adiciona ao que o preço já diz).

Ressalvas permanentes: a reconstrução usa o último preço negociado (mercados
finos de madrugada não executariam em tamanho); a calibração é reajustada
in-sample a cada rodada; e o arquivo cresce ~3 dias por execução, então os
números mudam conforme a história acumula.

## Limitações conhecidas / próximos passos

- O viés é uma média simples; separar por estação do ano, condição de céu e
  vento (gradient boosting) tende a melhorar.
- O nowcast é um deslocamento amortecido, não uma regressão treinada
  (Tmax ~ T das 9h/10h + nuvens + vento).
- Falta nowcasting de satélite (GOES-16) e radar para capar a máxima quando
  nebulosidade/chuva se aproxima — o METAR de nuvens já ajuda indiretamente
  via nowcast de temperatura.
- METAR brasileiro reporta temperatura em graus inteiros; confirme a regra de
  resolução do mercado (inteiro do METAR vs. décimos) antes de operar.
