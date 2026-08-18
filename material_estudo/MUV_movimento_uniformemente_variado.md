# Material de Reforço — Movimento Uniformemente Variado (MUV)

> Feito para estudar sozinho. Cada tópico tem: **(a)** teoria curta, **(b)** um
> exemplo resolvido passo a passo, **(c)** de 4 a 6 exercícios em ordem
> crescente de dificuldade e **(d)** o gabarito comentado no fim do tópico.
>
> **Como usar:** leia a teoria, acompanhe o exemplo com lápis na mão, tente os
> exercícios **sem olhar o gabarito** e só depois confira. Se errar, releia o
> comentário — quase todo erro em MUV é de *sinal* ou de *ler mal o enunciado*,
> não de conta.

---

## As três fórmulas que resolvem quase tudo

Usaremos **α** para a aceleração (alguns livros escrevem **a**). No MUV a
aceleração é **constante**.

| Nome | Fórmula | Serve para… |
|---|---|---|
| Função horária dos espaços | $s = s_0 + v_0\,t + \dfrac{\alpha}{2}\,t^2$ | achar a **posição** num instante $t$ |
| Função horária da velocidade | $v = v_0 + \alpha\,t$ | achar a **velocidade** num instante $t$ |
| Equação de Torricelli | $v^2 = v_0^2 + 2\,\alpha\,\Delta s$ | relacionar $v$ e $\Delta s$ **sem usar o tempo** |

O que cada símbolo significa:

- $s_0$ = **posição inicial** (onde o corpo está quando o cronômetro marca $t=0$ — leia na régua!).
- $s$ = posição num instante $t$ qualquer.
- $\Delta s = s - s_0$ = **deslocamento** (o quanto andou, não onde está).
- $v_0$ = **velocidade inicial** (em $t=0$).
- $v$ = velocidade num instante $t$.
- $\alpha$ = aceleração (constante).

### Convenção de sinais (a origem de 90% dos erros)

1. **Escolha uma orientação positiva** para a trajetória (ex.: para a direita).
2. $v_0$ é **positivo** se o corpo se move no sentido positivo; **negativo** se no sentido contrário.
3. $\alpha$ é **positivo** se aponta no sentido positivo; **negativo** se aponta no contrário.
4. **Acelerado** = $v$ e $\alpha$ têm o **mesmo sinal** (o módulo da velocidade cresce).
   **Retardado** = $v$ e $\alpha$ têm **sinais opostos** (o módulo da velocidade diminui).

> ⚠️ "Retardado" **não** quer dizer $\alpha$ negativo. Quer dizer $\alpha$ com
> sinal **contrário ao da velocidade**. Se o corpo anda no sentido negativo
> ($v_0<0$) e freia, a aceleração é **positiva**.

---

## Tópico 0 — Montar as funções a partir do enunciado

### Teoria

Antes de calcular qualquer coisa, extraia do enunciado, **com sinal**:
$s_0$, $v_0$ e $\alpha$. Depois é só substituir. Palavras-chave:

- "**parte do repouso**" → $v_0 = 0$ (e **só isso**; não diz nada sobre $s_0$).
- "**parte da origem**" → $s_0 = 0$.
- "**freia / desacelera / retardado**" → $\alpha$ com sinal contrário a $v_0$.
- "**velocidade constante**" → $\alpha = 0$ (aí não é MUV, é MU).

### Exemplo resolvido

> Um carro passa por $s_0 = 10\ \text{m}$ com $v_0 = 8\ \text{m/s}$ e acelera a
> $\alpha = 2\ \text{m/s}^2$ no sentido do movimento. Escreva as funções
> horárias e ache posição e velocidade em $t = 5\ \text{s}$.

**Passo 1 — identificar:** $s_0 = 10$, $v_0 = 8$, $\alpha = 2$ (tudo positivo).

**Passo 2 — funções horárias:**
$$s = 10 + 8t + \tfrac{2}{2}t^2 = 10 + 8t + t^2$$
$$v = 8 + 2t$$

**Passo 3 — em $t=5$:**
$$s = 10 + 8(5) + (5)^2 = 10 + 40 + 25 = 75\ \text{m}$$
$$v = 8 + 2(5) = 18\ \text{m/s}$$

### Exercícios

**0.1)** Um móvel parte da origem, do repouso, com $\alpha = 3\ \text{m/s}^2$.
Escreva $s(t)$ e $v(t)$.

**0.2)** Uma partícula tem $s_0 = -4\ \text{m}$, $v_0 = 6\ \text{m/s}$ e
$\alpha = 2\ \text{m/s}^2$. Ache $s$ e $v$ em $t = 3\ \text{s}$.

**0.3)** Um trem a $20\ \text{m/s}$ começa a frear com $\alpha = 4\ \text{m/s}^2$
(retardado). Escreva $v(t)$ com o sinal correto de $\alpha$.

**0.4)** Dada a função $s = 5 + 4t - t^2$ (SI), identifique $s_0$, $v_0$ e $\alpha$.

**0.5)** Um corpo anda no sentido **negativo** a $v_0 = -12\ \text{m/s}$ e freia
até parar. O módulo da desaceleração é $3\ \text{m/s}^2$. Qual o sinal de $\alpha$?
Escreva $v(t)$.

### Gabarito comentado — Tópico 0

- **0.1)** $s = \tfrac{3}{2}t^2 = 1{,}5\,t^2$ e $v = 3t$. (Origem → $s_0=0$; repouso → $v_0=0$.)
- **0.2)** $s = -4 + 6(3) + \tfrac{2}{2}(3)^2 = -4 + 18 + 9 = 23\ \text{m}$; $\;v = 6 + 2(3) = 12\ \text{m/s}$.
- **0.3)** Anda no sentido positivo e freia ⇒ $\alpha$ **negativo**: $v = 20 - 4t$.
- **0.4)** Comparando com $s_0 + v_0 t + \tfrac{\alpha}{2}t^2$: $s_0 = 5$, $v_0 = 4$
  e $\tfrac{\alpha}{2} = -1 \Rightarrow \alpha = -2\ \text{m/s}^2$. (Cuidado: o coeficiente de $t^2$ é $\alpha/2$, não $\alpha$.)
- **0.5)** Velocidade é negativa e o corpo **freia**, então $\alpha$ tem sinal
  **oposto**: $\alpha = +3\ \text{m/s}^2$. Logo $v = -12 + 3t$. (Ele para em $t=4\ \text{s}$.)

---

## Tópico 1 — Interpretar raízes negativas de tempo ($t < 0$)

### Teoria

Quando você pergunta "em que instante o corpo passa pela posição $s$?", cai numa
**equação do 2º grau em $t$**, que dá **duas raízes**. O que fazer com uma raiz
negativa depende **do enunciado**:

- Se a aceleração vale **"para qualquer instante $t$"** (o enunciado não obriga o
  movimento a começar em $t=0$), então **$t<0$ é um instante real**, só que
  **anterior** ao momento em que zeramos o cronômetro. A raiz negativa é válida.
- Se o enunciado diz que **o movimento começa em $t=0$** (ex.: "parte do repouso
  nesse instante", "é solto em $t=0$"), então **$t<0$ não existe** para esse
  movimento e você **descarta** a raiz negativa.

> 🔑 **Ideia central:** a "origem dos tempos" ($t=0$) é só o instante em que o
> **cronômetro foi zerado** — não é, necessariamente, o instante em que o
> movimento nasceu. $t=-1\ \text{s}$ significa "1 segundo **antes** de eu apertar
> o cronômetro", o que pode ser perfeitamente real.

### Exemplo resolvido

> A posição de uma partícula é $s = t^2 - 2t - 3$ (SI), **válida para qualquer
> instante $t$**. Em que instantes ela passa pela origem ($s = 0$)?

**Passo 1 — montar a equação:** $t^2 - 2t - 3 = 0$.

**Passo 2 — resolver** (Bháskara ou soma/produto): raízes cuja soma é $2$ e
produto $-3$ → $t = 3$ e $t = -1$.

**Passo 3 — interpretar:** como a função vale para **qualquer** $t$, **ambas**
são físicas. A partícula passou pela origem em $t = -1\ \text{s}$ (1 s **antes**
do instante zero) e de novo em $t = 3\ \text{s}$.

> Se o enunciado dissesse "o movimento começa em $t=0$", a resposta seria só
> $t = 3\ \text{s}$ e descartaríamos $t = -1\ \text{s}$.

### Exercícios

**1.1)** Resolva $s = t^2 - 5t + 6 = 0$. As duas raízes são fisicamente
aceitáveis mesmo se o movimento começou em $t=0$? Por quê?

**1.2)** A função $s = t^2 + 2t - 8$ (SI) vale para qualquer $t$. Em que
instantes o corpo passa por $s = 0$? Interprete a raiz negativa.

**1.3)** Um corpo tem $s = 12 - 4t + t^2$ (SI). Ele **começa a se mover em
$t = 0$**. Em que instante(s) ele passa por $s = 9\ \text{m}$?

**1.4)** A velocidade de um móvel é $v = -6 + 2t$ (SI), válida para qualquer $t$.
Em que instante $v = 0$? Antes desse instante o movimento é acelerado ou
retardado? (Dica: compare os sinais de $v$ e de $\alpha$.)

**1.5)** (desafio) $s = t^2 - 4t + 3$ vale para **qualquer** $t$. (a) Em que
instantes $s = 0$? (b) Em que instante a velocidade se anula? (c) Qual a menor
posição atingida (posição no instante em que $v=0$)?

### Gabarito comentado — Tópico 1

- **1.1)** Raízes $t = 2\ \text{s}$ e $t = 3\ \text{s}$. **Ambas positivas**,
  então são aceitáveis mesmo com o movimento começando em $t=0$. Aqui **nem
  surge** a discussão de raiz negativa — é o caso "tranquilo".
- **1.2)** $t^2 + 2t - 8 = 0 \Rightarrow t = 2$ e $t = -4$. Como a função vale
  para qualquer $t$, **as duas valem**: o corpo passou por $s=0$ em
  $t = -4\ \text{s}$ (4 s antes do cronômetro) e em $t = 2\ \text{s}$.
- **1.3)** $12 - 4t + t^2 = 9 \Rightarrow t^2 - 4t + 3 = 0 \Rightarrow t = 1$ e
  $t = 3$. Ambas são positivas ⇒ **ambas valem**: passa por $9\ \text{m}$ em
  $t = 1\ \text{s}$ (na ida) e $t = 3\ \text{s}$ (na volta, pois o movimento é
  retardado e ele volta). Não há raiz a descartar.
- **1.4)** $v = 0 \Rightarrow -6 + 2t = 0 \Rightarrow t = 3\ \text{s}$. Aqui
  $\alpha = +2$. Para $t<3$, $v<0$ (sinais **opostos** de $v$ e $\alpha$) ⇒
  **retardado**. Para $t>3$, $v>0$ (mesmo sinal) ⇒ **acelerado**. O instante
  $t=3\ \text{s}$ é a "inversão".
- **1.5)** (a) $t^2 - 4t + 3 = 0 \Rightarrow t = 1\ \text{s}$ e $t = 3\ \text{s}$.
  (b) $v = \dfrac{ds}{dt} = 2t - 4 = 0 \Rightarrow t = 2\ \text{s}$ (também dá para
  ver que é o **ponto médio** entre as raízes). (c) $s(2) = 4 - 8 + 3 = -1\ \text{m}$
  — é a posição mais recuada; depois o corpo volta.

---

## Tópico 2 — Ler $s_0$ na régua do esquema (e não confundir com repouso)

### Teoria

Dois erros clássicos que este tópico combate:

1. **Confundir "repouso" com $s_0 = 0$.**
   "Parte do repouso" fala **só** da velocidade: $v_0 = 0$.
   A posição inicial $s_0$ é **onde o corpo está** no instante $t=0$, e isso você
   **lê na régua** "$s\,(\text{m})$" do desenho. Pode ser $s_0 = 30\ \text{m}$
   mesmo o corpo estando parado.

2. **Confundir deslocamento com posição final.**
   - **Posição final** $s$ = a marca da régua onde ele para/chega.
   - **Deslocamento** $\Delta s = s - s_0$ = o quanto ele andou.
   Se a pergunta é "**em que ponto da régua** ele está?", a resposta é $s$. Se é
   "**quanto ele percorreu**?", é $\Delta s$. São perguntas diferentes.

### Exemplo resolvido

> No esquema, a régua marca o carrinho em $s_0 = 20\ \text{m}$. Ele **parte do
> repouso** e acelera a $\alpha = 4\ \text{m/s}^2$. Depois de $3\ \text{s}$:
> (a) em que ponto da régua ele está? (b) quanto ele andou?

**Passo 1 — dados com atenção à régua:** $s_0 = 20\ \text{m}$ (lido no desenho),
$v_0 = 0$ (repouso), $\alpha = 4$.

**Passo 2 — posição final:**
$$s = 20 + 0\cdot 3 + \tfrac{4}{2}(3)^2 = 20 + 18 = 38\ \text{m}$$

**Passo 3 — deslocamento:**
$$\Delta s = s - s_0 = 38 - 20 = 18\ \text{m}$$

**(a)** Está na marca **$38\ \text{m}$** da régua. **(b)** Andou **$18\ \text{m}$**.
Repare: se você tivesse "chutado" $s_0 = 0$, erraria a letra (a).

### Exercícios

**2.1)** Um bloco está em $s_0 = 15\ \text{m}$, parte do repouso e acelera a
$2\ \text{m/s}^2$. Onde está em $t = 4\ \text{s}$? Quanto andou?

**2.2)** Um objeto na marca $s_0 = -10\ \text{m}$ tem $v_0 = 5\ \text{m/s}$ e
$\alpha = 2\ \text{m/s}^2$. Em $t = 5\ \text{s}$, qual a **posição** e qual o
**deslocamento**?

**2.3)** Verdadeiro ou falso, justificando: "Como o corpo parte do repouso,
então $s_0 = 0$".

**2.4)** Um carro parte do repouso na marca $s_0 = 8\ \text{m}$ e, após andar,
para na marca $s = 8\ \text{m}$ de novo (foi e voltou). Quanto vale o
**deslocamento**? E a **distância percorrida** pode ser diferente dele?

**2.5)** (interpretação de gráfico/régua) Um móvel sai de $s_0 = 100\ \text{m}$
com $v_0 = -20\ \text{m/s}$ (sentido negativo) e $\alpha = +4\ \text{m/s}^2$.
(a) Qual sua posição em $t = 5\ \text{s}$? (b) O deslocamento nesse intervalo?

### Gabarito comentado — Tópico 2

- **2.1)** $s = 15 + \tfrac{2}{2}(4)^2 = 15 + 16 = 31\ \text{m}$ (posição na
  régua). $\Delta s = 31 - 15 = 16\ \text{m}$ (andou). $s_0$ **não** é zero só
  porque partiu do repouso.
- **2.2)** $s = -10 + 5(5) + \tfrac{2}{2}(5)^2 = -10 + 25 + 25 = 40\ \text{m}$
  (posição). $\Delta s = 40 - (-10) = 50\ \text{m}$ (deslocamento). Note que
  posição e deslocamento têm **valores diferentes** — não são a mesma coisa.
- **2.3)** **Falso.** "Repouso" só garante $v_0 = 0$. O valor de $s_0$ depende de
  **onde** o corpo está na régua, e isso vem do desenho/enunciado, não da
  velocidade.
- **2.4)** Deslocamento $\Delta s = s - s_0 = 8 - 8 = 0$. Já a **distância
  percorrida** (o comprimento total do caminho de ida e volta) é **maior que
  zero**. Deslocamento pode ser zero mesmo tendo andado bastante.
- **2.5)** (a) $s = 100 - 20(5) + \tfrac{4}{2}(5)^2 = 100 - 100 + 50 = 50\ \text{m}$.
  (b) $\Delta s = 50 - 100 = -50\ \text{m}$ (deslocamento **negativo**: líquido
  para o sentido negativo, apesar de já estar freando e voltando).

---

## Tópico 3 — Movimento uniformemente **retardado**

### Teoria

No movimento retardado, o módulo da velocidade **diminui**, então $\alpha$ tem
**sinal oposto** ao de $v_0$. Duas perguntas típicas:

**(A) Quando ele para?** Faça $v = 0$ na função da velocidade:
$$0 = v_0 + \alpha\,t \;\Rightarrow\; t_{\text{parada}} = -\dfrac{v_0}{\alpha}$$
(dá positivo porque $v_0$ e $\alpha$ têm sinais opostos).

**(B) Que distância percorre até parar?** Dois caminhos que devem dar o **mesmo**
resultado (use um como verificação do outro):

- **Torricelli** (atalho, sem $t$): $0 = v_0^2 + 2\alpha\,\Delta s \Rightarrow \Delta s = -\dfrac{v_0^2}{2\alpha} = \dfrac{v_0^2}{2\,|\alpha|}$.
- **Função horária**, substituindo o $t_{\text{parada}}$ achado em (A).

### Exemplo resolvido

> Um carro a $v_0 = 20\ \text{m/s}$ freia com desaceleração de módulo
> $4\ \text{m/s}^2$. (a) Em quanto tempo para? (b) Que distância percorre até
> parar?

**Passo 1 — sinal de $\alpha$:** anda no sentido positivo e freia ⇒ $\alpha = -4\ \text{m/s}^2$.

**Passo 2 — tempo de parada:** $0 = 20 + (-4)t \Rightarrow t = 5\ \text{s}$.

**Passo 3 — distância (Torricelli):**
$$0 = 20^2 + 2(-4)\Delta s \Rightarrow 0 = 400 - 8\Delta s \Rightarrow \Delta s = 50\ \text{m}$$

**Verificação pela função horária** (com $t=5$):
$$\Delta s = v_0 t + \tfrac{\alpha}{2}t^2 = 20(5) + \tfrac{-4}{2}(5)^2 = 100 - 50 = 50\ \text{m}\ \checkmark$$

Os dois métodos batem → resposta confiável.

### Exercícios

**3.1)** Uma moto a $30\ \text{m/s}$ freia a $5\ \text{m/s}^2$ (retardado). Tempo
até parar?

**3.2)** No exercício 3.1, qual a distância percorrida até parar? Resolva
**pelos dois métodos** (Torricelli e função horária) e confira.

**3.3)** Um trem a $15\ \text{m/s}$ leva $10\ \text{s}$ para parar. Qual foi a
desaceleração (módulo e sinal)? Que distância percorreu?

**3.4)** Um corpo se move no **sentido negativo** com $v_0 = -8\ \text{m/s}$ e
freia até parar; o módulo da aceleração é $2\ \text{m/s}^2$. (a) Qual o **sinal**
de $\alpha$? (b) Tempo de parada? (c) Deslocamento até parar (com sinal)?

**3.5)** Um automóvel a $24\ \text{m/s}$ precisa parar em, no máximo,
$36\ \text{m}$. Qual o **módulo mínimo** da desaceleração necessária?

**3.6)** (desafio) Um carro a $v_0$ freia a $6\ \text{m/s}^2$ e para em
$48\ \text{m}$. Qual era $v_0$? Quanto tempo levou?

### Gabarito comentado — Tópico 3

- **3.1)** $\alpha = -5$. $\;0 = 30 - 5t \Rightarrow t = 6\ \text{s}$.
- **3.2)** Torricelli: $0 = 30^2 - 2(5)\Delta s \Rightarrow \Delta s = \dfrac{900}{10} = 90\ \text{m}$.
  Função horária: $\Delta s = 30(6) + \tfrac{-5}{2}(6)^2 = 180 - 90 = 90\ \text{m}$. **Batem** ✅.
- **3.3)** $0 = 15 + \alpha(10) \Rightarrow \alpha = -1{,}5\ \text{m/s}^2$
  (módulo $1{,}5$, sinal negativo). Distância: $\Delta s = 15(10) + \tfrac{-1{,}5}{2}(10)^2 = 150 - 75 = 75\ \text{m}$.
- **3.4)** (a) Velocidade negativa e freando ⇒ $\alpha$ **positivo**: $+2\ \text{m/s}^2$.
  (b) $0 = -8 + 2t \Rightarrow t = 4\ \text{s}$.
  (c) $\Delta s = -8(4) + \tfrac{2}{2}(4)^2 = -32 + 16 = -16\ \text{m}$
  (deslocou-se $16\ \text{m}$ no sentido negativo).
- **3.5)** Torricelli com $v=0$: $0 = 24^2 - 2|\alpha|(36) \Rightarrow |\alpha| = \dfrac{576}{72} = 8\ \text{m/s}^2$.
- **3.6)** Torricelli: $0 = v_0^2 - 2(6)(48) \Rightarrow v_0^2 = 576 \Rightarrow v_0 = 24\ \text{m/s}$.
  Tempo: $0 = 24 - 6t \Rightarrow t = 4\ \text{s}$.

---

## Tópico 4 — Achar $\alpha$ (e depois $v$) a partir do espaço percorrido

### Teoria

Caso muito comum: a partícula **parte do repouso da origem** ($v_0 = 0$,
$s_0 = 0$) e o enunciado dá **quanto ela andou** ($\Delta s$) em um **tempo** $t$.
A função dos espaços vira:
$$\Delta s = \tfrac{\alpha}{2}\,t^2 \;\Rightarrow\; \boxed{\alpha = \dfrac{2\,\Delta s}{t^2}}$$

Com $\alpha$ na mão, a velocidade naquele instante sai de:
$$v = v_0 + \alpha\,t = \alpha\,t \quad(\text{pois } v_0 = 0)$$

> Estratégia geral: **isolar** da mesma fórmula a variável que o problema pede.
> A função horária dos espaços tem quatro "personagens" ($\Delta s$, $v_0$,
> $\alpha$, $t$); se o enunciado dá três, você isola o quarto.

### Exemplo resolvido

> Uma partícula parte do repouso e percorre $100\ \text{m}$ em $5\ \text{s}$ com
> aceleração constante. (a) Qual a aceleração? (b) Qual a velocidade ao fim dos
> $5\ \text{s}$?

**Passo 1 — dados:** $v_0 = 0$, $s_0 = 0$, $\Delta s = 100$, $t = 5$.

**Passo 2 — isolar $\alpha$:**
$$100 = \tfrac{\alpha}{2}(5)^2 = 12{,}5\,\alpha \;\Rightarrow\; \alpha = \dfrac{100}{12{,}5} = 8\ \text{m/s}^2$$

**Passo 3 — velocidade final:** $v = \alpha t = 8(5) = 40\ \text{m/s}$.

**Verificação (Torricelli):** $v^2 = 0 + 2(8)(100) = 1600 \Rightarrow v = 40\ \text{m/s}$ ✅.

### Exercícios

**4.1)** Parte do repouso e anda $18\ \text{m}$ em $3\ \text{s}$. Ache $\alpha$ e a
velocidade final.

**4.2)** Parte do repouso, aceleração $a$, e percorre $80\ \text{m}$ em
$4\ \text{s}$. Ache $a$ e $v$ ao fim.

**4.3)** Parte do repouso e atinge $30\ \text{m/s}$ em $6\ \text{s}$.
(a) Qual a aceleração? (b) Que distância percorreu? (Faça por dois caminhos.)

**4.4)** Uma partícula **não** parte do repouso: tem $v_0 = 4\ \text{m/s}$,
percorre $40\ \text{m}$ em $4\ \text{s}$. Ache $\alpha$ e a velocidade final.
(Dica: agora $\Delta s = v_0 t + \tfrac{\alpha}{2}t^2$.)

**4.5)** (desafio) Parte do repouso; nos **primeiros** $2\ \text{s}$ percorre
$6\ \text{m}$. Que distância percorre nos **primeiros** $4\ \text{s}$? (E só no
**segundo** intervalo de 2 s, isto é, entre $t=2$ e $t=4$?)

### Gabarito comentado — Tópico 4

- **4.1)** $18 = \tfrac{\alpha}{2}(3)^2 = 4{,}5\alpha \Rightarrow \alpha = 4\ \text{m/s}^2$;
  $v = 4(3) = 12\ \text{m/s}$.
- **4.2)** $80 = \tfrac{a}{2}(4)^2 = 8a \Rightarrow a = 10\ \text{m/s}^2$;
  $v = 10(4) = 40\ \text{m/s}$.
- **4.3)** (a) $30 = \alpha(6) \Rightarrow \alpha = 5\ \text{m/s}^2$.
  (b) Função horária: $\Delta s = \tfrac{5}{2}(6)^2 = 90\ \text{m}$;
  Torricelli: $30^2 = 2(5)\Delta s \Rightarrow \Delta s = \dfrac{900}{10} = 90\ \text{m}$ ✅.
- **4.4)** $40 = 4(4) + \tfrac{\alpha}{2}(4)^2 = 16 + 8\alpha \Rightarrow 8\alpha = 24 \Rightarrow \alpha = 3\ \text{m/s}^2$;
  $v = 4 + 3(4) = 16\ \text{m/s}$.
- **4.5)** Primeiro ache $\alpha$: $6 = \tfrac{\alpha}{2}(2)^2 = 2\alpha \Rightarrow \alpha = 3\ \text{m/s}^2$.
  Em $4\ \text{s}$: $\Delta s = \tfrac{3}{2}(4)^2 = 24\ \text{m}$.
  Só no segundo intervalo: $24 - 6 = 18\ \text{m}$. (Repare que, no MUV partindo do
  repouso, cada intervalo igual de tempo cobre distâncias na proporção
  $1 : 3 : 5 : 7\ldots$ — aqui $6$ e $18$ estão na razão $1:3$.)

---

## Revisão final — exercícios misturados

Sem "dica de tópico": você tem que decidir qual fórmula usar. Gabarito logo abaixo.

**R1)** Um móvel tem $s = 8 - 6t + t^2$ (SI), válido para qualquer $t$.
(a) $s_0$, $v_0$, $\alpha$? (b) Quando passa por $s=0$? (c) Quando para?

**R2)** Carro a $25\ \text{m/s}$ freia a $5\ \text{m/s}^2$. Tempo e distância até parar.

**R3)** Parte do repouso na marca $s_0 = 12\ \text{m}$, $\alpha = 2\ \text{m/s}^2$.
Posição e deslocamento em $t = 6\ \text{s}$.

**R4)** Parte do repouso e percorre $50\ \text{m}$ em $5\ \text{s}$. Aceleração e
velocidade final.

**R5)** Corpo com $v_0 = -10\ \text{m/s}$ e $\alpha = +2\ \text{m/s}^2$ (para
qualquer $t$). (a) Quando $v=0$? (b) Antes disso é acelerado ou retardado?
(c) Posição em $t=8\ \text{s}$ se $s_0 = 0$.

### Gabarito comentado — Revisão

- **R1)** (a) $s_0 = 8$, $v_0 = -6$, $\tfrac{\alpha}{2}=1 \Rightarrow \alpha = 2$.
  (b) $t^2 - 6t + 8 = 0 \Rightarrow t = 2\ \text{s}$ e $t = 4\ \text{s}$ (ambas
  válidas). (c) $v = -6 + 2t = 0 \Rightarrow t = 3\ \text{s}$.
- **R2)** $\alpha = -5$: $0 = 25 - 5t \Rightarrow t = 5\ \text{s}$;
  $\Delta s = \dfrac{25^2}{2\cdot 5} = \dfrac{625}{10} = 62{,}5\ \text{m}$.
- **R3)** $s = 12 + \tfrac{2}{2}(6)^2 = 12 + 36 = 48\ \text{m}$ (posição);
  $\Delta s = 48 - 12 = 36\ \text{m}$ (deslocamento).
- **R4)** $50 = \tfrac{\alpha}{2}(5)^2 = 12{,}5\alpha \Rightarrow \alpha = 4\ \text{m/s}^2$;
  $v = 4(5) = 20\ \text{m/s}$.
- **R5)** (a) $0 = -10 + 2t \Rightarrow t = 5\ \text{s}$. (b) Antes de $t=5$,
  $v<0$ e $\alpha>0$ (sinais opostos) ⇒ **retardado**. (c) $s = 0 - 10(8) + \tfrac{2}{2}(8)^2 = -80 + 64 = -16\ \text{m}$.

---

## Checklist antes de entregar qualquer questão de MUV

1. Escolhi a **orientação positiva** e escrevi $s_0$, $v_0$, $\alpha$ **com sinal**?
2. "Repouso" eu tratei como $v_0=0$ (e **não** como $s_0=0$)?
3. Se é **retardado**, o $\alpha$ está com sinal **oposto** ao de $v_0$?
4. A pergunta quer **posição** ($s$) ou **deslocamento** ($\Delta s = s-s_0$)?
5. Se apareceu raiz **negativa** de tempo: o enunciado permite $t<0$ ou manda
   começar em $t=0$?
6. Dá para **conferir** com a equação de Torricelli (ou pelo outro método)?

> Bons estudos! Refaça os exercícios em que errar **sem olhar o gabarito** —
> em MUV, o segundo acerto é o que fixa.
