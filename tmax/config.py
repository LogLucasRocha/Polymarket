"""Configuração central do pipeline de previsão de temperatura máxima."""
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Diretórios
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


@dataclass(frozen=True)
class Station:
    """Local usado para prever e observar o mercado de temperatura.

    ``icao`` continua sendo a identidade usada no slug do Polymarket. Alguns
    links do Wunderground, porém, entregam observações de outra estação física;
    nesses casos os campos ``wu_*`` registram a fonte que realmente liquida o
    contrato e ``lat``/``lon`` apontam para essa fonte.
    """

    icao: str
    city: str      # nome curto usado em títulos e abas
    airport: str   # nome completo do aeroporto
    flag: str      # emoji da bandeira, para as abas do painel
    lat: float
    lon: float
    timezone: str
    unit: str = "C"   # unidade em que o mercado da cidade resolve (C ou F)
    wu_history_url: str | None = None
    wu_location_id: str | None = None
    wu_observation_id: str | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def label(self) -> str:
        return f"{self.city} ({self.icao})"

    @property
    def bias_cache_file(self) -> Path:
        return DATA_DIR / f"bias_cache_{self.icao}.json"


# A fonte de cada cidade parte da descrição oficial do mercado no Polymarket.
# Quando a página apontada entrega outra estação física, prevalece a série que
# realmente aparece na tabela de resolução. check_resolution_sources() confere
# os links; o coletor WU também valida o identificador interno da observação.
# Hong Kong ficou de fora de propósito: resolve diretamente pelo Observatório
# de HK, não por uma estação METAR.
STATIONS = {
    "SBGR": Station("SBGR", "Guarulhos", "São Paulo/Guarulhos Intl",
                    "🇧🇷", -23.4356, -46.4731, "America/Sao_Paulo"),
    "SAEZ": Station("SAEZ", "Buenos Aires", "Ministro Pistarini (Ezeiza)",
                    "🇦🇷", -34.8222, -58.5358,
                    "America/Argentina/Buenos_Aires"),
    "UUWW": Station("UUWW", "Moscou", "Moscou/Vnukovo Intl",
                    "🇷🇺", 55.5915, 37.2615, "Europe/Moscow"),
    "CYYZ": Station("CYYZ", "Toronto", "Toronto Pearson Intl",
                    "🇨🇦", 43.6772, -79.6306, "America/Toronto"),
    "MMMX": Station("MMMX", "Cidade do México", "Benito Juárez Intl",
                    "🇲🇽", 19.4363, -99.0721, "America/Mexico_City"),
    "EGLC": Station("EGLC", "Londres", "London City",
                    "🇬🇧", 51.5053, 0.0553, "Europe/London"),
    "LFPB": Station("LFPB", "Paris", "Paris-Le Bourget",
                    "🇫🇷", 48.9694, 2.4414, "Europe/Paris"),
    "LEMD": Station("LEMD", "Madri", "Adolfo Suárez Madrid-Barajas",
                    "🇪🇸", 40.4719, -3.5626, "Europe/Madrid"),
    "EHAM": Station("EHAM", "Amsterdã", "Schiphol",
                    "🇳🇱", 52.3086, 4.7639, "Europe/Amsterdam"),
    "EPWA": Station("EPWA", "Varsóvia", "Warsaw Chopin",
                    "🇵🇱", 52.1657, 20.9671, "Europe/Warsaw"),
    "LTFM": Station("LTFM", "Istambul", "Istanbul Airport",
                    "🇹🇷", 41.2753, 28.7519, "Europe/Istanbul"),
    "LTAC": Station("LTAC", "Ancara", "Esenboğa Intl",
                    "🇹🇷", 40.1281, 32.9951, "Europe/Istanbul"),
    "RKSI": Station("RKSI", "Seul", "Incheon Intl",
                    "🇰🇷", 37.4692, 126.4505, "Asia/Seoul"),
    "RJTT": Station("RJTT", "Tóquio", "Haneda",
                    "🇯🇵", 35.5533, 139.7811, "Asia/Tokyo"),
    "ZBAA": Station("ZBAA", "Pequim", "Beijing Capital Intl",
                    "🇨🇳", 40.0801, 116.5846, "Asia/Shanghai"),
    "ZSPD": Station("ZSPD", "Xangai", "Shanghai Pudong Intl",
                    "🇨🇳", 31.1434, 121.8052, "Asia/Shanghai"),
    "WSSS": Station("WSSS", "Singapura", "Changi",
                    "🇸🇬", 1.3502, 103.9944, "Asia/Singapore"),
    "NZWN": Station("NZWN", "Wellington", "Wellington Intl",
                    "🇳🇿", -41.3272, 174.8053, "Pacific/Auckland"),
    "LIMC": Station("LIMC", "Milão", "Malpensa Intl",
                    "🇮🇹", 45.6306, 8.7281, "Europe/Rome"),
    "ZHHH": Station("ZHHH", "Wuhan", "Wuhan Tianhe Intl",
                    "🇨🇳", 30.7838, 114.2081, "Asia/Shanghai"),
    "EDDM": Station("EDDM", "Munique", "Munich Airport",
                    "🇩🇪", 48.3538, 11.7861, "Europe/Berlin"),
    "EFHK": Station("EFHK", "Helsinque", "Helsinki-Vantaa",
                    "🇫🇮", 60.3172, 24.9633, "Europe/Helsinki"),
    "LLBG": Station("LLBG", "Tel Aviv", "Ben Gurion Intl",
                    "🇮🇱", 32.0114, 34.8867, "Asia/Jerusalem"),
    "RPLL": Station("RPLL", "Manila", "Ninoy Aquino Intl",
                    "🇵🇭", 14.5086, 121.0195, "Asia/Manila"),
    "WMKK": Station("WMKK", "Kuala Lumpur", "Kuala Lumpur Intl",
                    "🇲🇾", 2.7456, 101.7099, "Asia/Kuala_Lumpur"),
    "RCSS": Station("RCSS", "Taipé", "Taipei Songshan",
                    "🇹🇼", 25.0694, 121.5519, "Asia/Taipei"),
    "ZGGG": Station("ZGGG", "Guangzhou", "Baiyun Intl",
                    "🇨🇳", 23.3924, 113.2988, "Asia/Shanghai"),
    "ZUUU": Station("ZUUU", "Chengdu", "Shuangliu Intl",
                    "🇨🇳", 30.5785, 103.9471, "Asia/Shanghai"),
    "FACT": Station("FACT", "Cidade do Cabo", "Cape Town Intl",
                    "🇿🇦", -33.9648, 18.6017, "Africa/Johannesburg"),
}

# Cidades cujos contratos resolvem em FAHRENHEIT. O grupo continua identificado
# separadamente para os estudos de temperatura mínima, mas desde 26/07 também
# faz parte da Ceifa ativa de temperatura máxima.
STATIONS_FAHRENHEIT = {
    "KLGA": Station("KLGA", "Nova York", "LaGuardia",
                    "🇺🇸", 40.7772, -73.8726, "America/New_York", unit="F"),
    "KORD": Station("KORD", "Chicago", "O'Hare Intl",
                    "🇺🇸", 41.9786, -87.9048, "America/Chicago", unit="F"),
    "KMIA": Station("KMIA", "Miami", "Miami Intl",
                    "🇺🇸", 25.7932, -80.2906, "America/New_York", unit="F"),
    "KLAX": Station("KLAX", "Los Angeles", "Los Angeles Intl",
                    "🇺🇸", 33.9425, -118.4081, "America/Los_Angeles",
                    unit="F"),
    "KDAL": Station("KDAL", "Dallas", "Dallas Love Field",
                    "🇺🇸", 32.8471, -96.8518, "America/Chicago", unit="F"),
    "KATL": Station("KATL", "Atlanta", "Hartsfield-Jackson Intl",
                    "🇺🇸", 33.6367, -84.4281, "America/New_York", unit="F"),
    "KBKF": Station("KBKF", "Denver", "Buckley SFB (Aurora)",
                    "🇺🇸", 39.7017, -104.7517, "America/Denver", unit="F"),
    "KHOU": Station("KHOU", "Houston", "William P. Hobby",
                    "🇺🇸", 29.6454, -95.2789, "America/Chicago", unit="F"),
    "KSEA": Station("KSEA", "Seattle", "Seattle-Tacoma Intl",
                    "🇺🇸", 47.4489, -122.3094, "America/Los_Angeles",
                    unit="F"),
}

# Universo operacional único da temperatura máxima: cidades em °C e °F.
STATIONS.update(STATIONS_FAHRENHEIT)

# Grupo de observação encerrado em 26/07: as 12 cidades foram promovidas para
# STATIONS e agora participam da Ceifa principal (captura, alertas e backtest).
# Mantido vazio temporariamente para compatibilidade com leitores antigos.
STATIONS_OBSERVE = {}

DEFAULT_STATION = STATIONS["SBGR"]

# Modelos determinísticos (Open-Meteo) -> família usada na correção de viés
DET_MODELS = {
    "ecmwf_ifs025": "ecmwf",
    "gfs_seamless": "gfs",
    "icon_seamless": "icon",
    "gem_seamless": "gem",
    "ecmwf_aifs025_single": "aifs",
}

# Nomes amigáveis para o relatório
MODEL_LABELS = {
    "ecmwf_ifs025": "ECMWF IFS",
    "gfs_seamless": "NOAA GFS",
    "icon_seamless": "DWD ICON",
    "gem_seamless": "CMC GEM",
    "ecmwf_aifs025_single": "ECMWF AIFS (IA)",
}

# Modelos de ensemble (Open-Meteo ensemble API) -> família de viés
ENS_MODELS = {
    "ecmwf_ifs025": "ecmwf",   # ENS: 51 membros
    "gfs05": "gfs",            # GEFS: 31 membros
}

# A API canonicaliza os nomes dos modelos nas chaves da resposta
ENS_RESPONSE_ALIASES = {
    "ecmwf_ifs025_ensemble": "ecmwf_ifs025",
    "ncep_gefs05": "gfs05",
}

# Correção de viés
BIAS_LOOKBACK_DAYS = 60          # janela de histórico para aprender o viés
BIAS_CACHE_MAX_AGE_HOURS = 24    # recalcula 1x/dia
MIN_OBS_PER_DAY = 18             # mínimo de METARs no dia para validar a máxima observada

# Nowcast intradiário
NOWCAST_DAMPING = 0.7            # fração do desvio observado aplicada às horas restantes
NOWCAST_HOURS = 3                # quantas horas recentes usar no cálculo do desvio

# Máxima do dia considerada "travada" quando as últimas N horas observadas
# ficaram todas abaixo dela (o pico passou); o digest corta o hora a hora
# restante de hoje.
TMAX_LOCK_HOURS = 3

# Estado do último digest enviado (para omitir estações sem novidade)
DIGEST_STATE_FILE = DATA_DIR / "digest_state.json"

# Estratégia Edge PAUSADA (decisão do Lucas, 14/07): foco só na colheita.
# Com False, o digest não computa nem envia sinais de edge; a colheita e o
# resto (posições, stop, avisos de condição, captura) seguem normais. A captura
# de mercado/previsão continua, então dá para reconstruir o edge depois. Para
# reativar, volte para True.
EDGE_ENABLED = False

# Sinal de edge: divergência mínima |projetado − mercado| numa faixa do dia
# operável (D0; D+1 quando a máxima de hoje já travou) que dispara a mensagem
# de alerta. Cada faixa avisa uma vez ao cruzar o corte e re-arma quando cai
# abaixo dele (ou na virada do dia).
EDGE_ALERT_MIN = 0.05

# NOTA (13/07): testamos um TETO de 20 p.p. ("edge grande demais = erro do
# modelo") e o backtest o rejeitou — no lado NÃO já filtrado, gap grande é o
# modelo vendo a máxima da tarde antes do mercado, não erro. Ver o histórico
# do git / relatório da sessão. Mantido sem teto de propósito.

# Confiança mínima do lado indicado pelo sinal: só alerta quando a projeção
# dá mais de 90% de chance de a aposta sugerida acertar (P(Yes) se o Yes está
# barato; 1 − P(Yes) se está caro).
EDGE_MIN_CONFIDENCE = 0.90

# Janela LOCAL em que sinais podem ser enviados. Fora dela (madrugada) o
# mercado é fino demais para executar e o backtest mostrou que o edge é
# ilusório: cruzamentos fora da janela são consumidos em silêncio — não
# ficam represados esperando a janela abrir.
SIGNAL_HOURS = (6, 23)

# Lados operados nos sinais de entrada: o backtest de 18 cidades mostrou 90%
# de acerto no NÃO contra 41% no SIM (perfil de loteria) — só o NÃO notifica.
SIGNAL_SIDES = ("NAO",)

# Preço mínimo do NÃO para sinalizar: NÃO abaixo disso é brigar com um
# mercado quase-certo do Yes — a autópsia mostrou que esses casos são
# cara-ou-coroa com book fino (Moscou 08/07 perdeu a $0.04; SAEZ 27/06
# ganhou a $0.08 por sorte). Com o filtro: 92% de acerto, drawdown 1.5%.
NAO_MIN_PRICE = 0.30

# Stop loss: alerta quando o mercado precifica a posição este percentual (ou
# mais) abaixo do preço médio de entrada — repetido a cada rodada enquanto
# durar. No backtest, a saída simulada acontece a STOP_EXIT_FRAC de perda.
STOP_ALERT_FRAC = 0.10
STOP_EXIT_FRAC = 0.15

# Alertas de condição observada (platô "andou de lado" e fuga do envelope do
# ensemble "acima do teto / abaixo do piso"). DESLIGADOS (decisão do Lucas,
# 16/07). Para reativar, True.
COND_ALERTS_ENABLED = False

# Resumo automático das posições abertas no Telegram. As posições continuam
# sendo consultadas para os controles internos e ficam disponíveis no relatório
# solicitado manualmente por cidade, mas o resumo periódico não é enviado.
POSITIONS_SUMMARY_ENABLED = False

# ---------------------------------------------------------------- Ceifa
# Estratégia ATIVA (decisão do Lucas, 15/07) e a ÚNICA no momento: comprar o
# NÃO quando o mercado já está quase-certo, com o preço do NÃO nesta faixa —
# a análise da base de mercado mostrou o mercado ~100% assertivo aí. O preço
# identifica a oportunidade; H-1 e os filtros abaixo autorizam a entrada. O
# alerta REPETE a cada rodada até você ter posição naquele contrato; assim que
# a carteira mostra a
# entrada, para de alertar aquele contrato. A banda de preço abre a oportunidade;
# H-1 e os vetos meteorológicos abaixo decidem se ela pode virar alerta.
# Assertividade = preço do NÃO convergindo para 1,0 (o NÃO resolveu).
CEIFA_ENABLED = True
CEIFA_MINIMUM_ENABLED = True  # NÃO de mínimas promovido em 31/07/2026
CEIFA_MINIMUM_TAF_FILTER = True  # bloqueia TSRA/VCTS no restante do dia local
CEIFA_PRICE_MIN = 0.95      # exclusivo: preço do NÃO > 0,95
CEIFA_PRICE_MAX = 0.995     # exclusivo: preço do NÃO < 0,995
CEIFA_STAKE_FRAC = 0.01     # parcela relativa ao saldo livre de cada rodada
CEIFA_REPEAT_MINUTES = 5    # nova parcela em cada rodada elegível

# FILTRO DE INCERTEZA (decisão do Lucas, 22/07 — substitui o stop loss no
# backtest). A Ceifa vende quase-certeza; em dia de ensemble muito largo na H-1
# (teto_ens − mediana grande) o estouro é possível (ex.: Istambul 22/07, spread
# 4,9 vs ~1,6 normal → máxima foi a 34 e o NÃO virou zero). Então NÃO entramos
# quando o spread está alto. STOP desligado: o filtro não depende de reagir ao
# gap, ele simplesmente não entra no dia perigoso.
CEIFA_STOP_ENABLED = False       # stop no backtest (desligado — filtro no lugar)
CEIFA_SPREAD_FILTER = True        # liga o filtro de incerteza
CEIFA_SPREAD_ABS = 3.0            # corta se spread na H-1 >= isto (°C)
CEIFA_SPREAD_REL = 2.0            # ou se spread >= REL × mediana da cidade

# Segundo veto de incerteza (decisão do Lucas, 26/07): em dia que está rodando
# claramente mais quente que o ensemble, não vender uma faixa que ainda está
# dentro da região plausível da máxima. Corta se o desvio observado bruto OU o
# shift chega a +1°C. Londres 27°C tinha desvio +1,7°C e shift +1,2°C; a largura
# do ensemble continuou normal porque teto e mediana subiram juntos.
CEIFA_NOWCAST_FILTER = True
CEIFA_OBS_DEVIATION_MIN = 1.0     # desvio bruto observado vs ensemble (°C)
CEIFA_NOWCAST_SHIFT_MIN = 1.0     # ajuste quente mínimo (°C)
CEIFA_TARGET_MARGIN = 0.5         # faixa: mediana−margem até P90+margem (°C)
CEIFA_PLATEAU_HOURS = 2.0         # mesma máxima observada por pelo menos 2h

# Não vende uma faixa que toque ou se sobreponha ao intervalo P10–P90 do
# ensemble. A comparação respeita o bucket de resolução: 32°C cobre valores
# contínuos de 31,5°C a 32,5°C; o toque em qualquer borda também bloqueia.
# RELIGADO (decisão do Lucas, 07/08): a tela de erros mostrou que ele pegaria
# 22 dos 23 erros de máximas — priorizamos cortar o risco.
CEIFA_ENSEMBLE_BAND_FILTER = True

# Contratos superiores abertos ("X°C or higher") perdem com qualquer pico
# acima de X. Mesmo quando X fica pouco acima do membro mais quente, um erro
# pequeno ou a discretização horária pode zerar o NÃO. Ex.: Chengdu 29/07:
# X=34°C e teto=33,33°C; a diferença de 0,67°C não era margem suficiente.
CEIFA_UPPER_TAIL_FILTER = True
CEIFA_UPPER_TAIL_MARGIN = 0.75     # bloqueia se X ≤ teto do ensemble + margem

# Cauda FRIA das mínimas (simétrico da cauda superior das máximas). A banda
# P10–P90 ignora o intervalo entre o piso do ensemble (membro mais frio) e o
# P10 — foi exatamente onde a mínima do EGLC 08/08 perdeu: faixa NÃO 14°C com
# piso 14,47°C (que arredonda para 14) e P10 14,78°C; a banda [14,78–15,0] não
# encostava na faixa, mas o piso estava dentro dela. Este filtro veta o NÃO
# quando a faixa vendida entra na cauda fria (piso − margem até P10).
# Margem cirúrgica: 0,25°C abaixo do piso. Em 0,5°C o filtro vira um penhasco
# (bloqueava 226 no histórico); 0,25°C pega o EGLC e cai para ~86, dando uma
# folga pequena para mínimas que furam abaixo do membro mais frio (a real do
# EGLC foi 0,47°C abaixo do piso).
CEIFA_LOWER_TAIL_FILTER = True
CEIFA_LOWER_TAIL_MARGIN = 0.25

# Veto de livro largo: num binário saudável, ask do Sim + ask do Não ≈ 100¢ (as
# duas pontas são complementares). Quando o Não está na banda (~96¢) mas o Sim
# TAMBÉM está caro, o livro está largo/ilíquido e o preço do Não não é uma
# probabilidade confiável — é só uma ordem de venda larga. Ex.: 25°C com Sim 51¢
# + Não 92¢ = 143¢ (overround 43¢) o mercado ainda dá ~51% ao bucket. Corta a
# entrada quando (ask_sim + ask_nao − 100¢) passa do limite. Calibrado no
# histórico de mínimas: p99 do overround é 6¢; 8¢ bloqueia só ~0,7% das entradas
# (os livros claramente quebrados) sem tocar no fluxo colado.
CEIFA_WIDE_BOOK_FILTER = True
CEIFA_WIDE_BOOK_MAX_OVERROUND = 0.08

# ----------------------------------------------------------- Execução automática
# Executor de ordens da Ceifa via API (CLOB). SEGURANÇA EM CAMADAS:
#  1. Desligado por padrão (CEIFA_EXEC_ENABLED=False) — nada é executado.
#  2. Mesmo ligado, começa em DRY-RUN (só registra o que faria).
#  3. Um arquivo "kill switch" (CEIFA_EXEC_KILL_FILE) aborta tudo se existir.
#  4. Limite por ordem e teto de exposição total.
# A chave da carteira NUNCA fica no código: vem de POLYMARKET_PRIVATE_KEY.
# Todos os valores podem ser sobrescritos por variável de ambiente de mesmo nome.
CEIFA_EXEC_ENABLED = False          # liga o executor (ainda respeita o dry-run)
CEIFA_EXEC_DRY_RUN = True           # True = só registra; False = envia de verdade
CEIFA_EXEC_MAX_STAKE_USD = 10.0     # teto de cada ordem individual (USDC)
CEIFA_EXEC_MAX_EXPOSURE_USD = 200.0  # teto do total já executado no dia
CEIFA_EXEC_KILL_FILE = "STOP_EXECUTOR"  # se este arquivo existir, aborta
CEIFA_EXEC_LEDGER = "data/executor_ledger.json"  # registro de ordens (idempotência)
CEIFA_EXEC_SIGNALS = "data/executor_signals.json"  # sinais gravados pelo send_telegram
CEIFA_EXEC_CHAIN_ID = 137           # Polygon

# Colheita de favoritos: APOSENTADA (decisão do Lucas 15/07 — substituída pela
# Ceifa). Mantida no código, desligada por HARVEST_ENABLED. Parâmetros antigos
# preservados só para o backtest histórico de comparação.
HARVEST_ENABLED = False
HARVEST_PRICE_MIN = 0.97
HARVEST_PRICE_MAX = 0.995
HARVEST_MIN_HOUR = 16
HARVEST_MIN_CONF = 0.85

# Com 39 cidades, o bloco completo (posições+tabela+gráfico+hora a hora) só
# é enviado para cidades com ATIVIDADE (posição aberta ou sinal na rodada);
# as demais são monitoradas em silêncio. False = comportamento antigo.
FULL_BLOCK_ONLY_WITH_ACTIVITY = True

# Inflação de incerteza para D+1 (erro cresce com o horizonte)
D1_STD_INFLATION = 1.15

USER_AGENT = "tmax-pipeline/1.0 (uso pessoal, previsao de temperatura)"
