:: terminal 1 — servidor
cd server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

:: terminal 2 — dispositivo virtual, na raiz do projeto
server\.venv\Scripts\python simulator\virtual_device.py --source 0 --interactive

server\.venv\Scripts\python simulator\virtual_device.py --source 0 --preview --sonar clear

# Roteiro de validação

Procedimentos para levantar os dados quantitativos que a metodologia do TCC
exige. Todos podem ser executados sem o protótipo montado; os que exigem
hardware estão marcados.

## 0. Preparação

```bash
cd server
uv run pytest -q            # 98 testes automatizados
uv run ruff check app
```

Para os ensaios, ligue o registro de eventos e comece com o arquivo limpo:

```bash
rm -f server/var/events.jsonl
AVS_STORAGE__LOG_EVENTS=true uv run uvicorn app.main:app --port 8000
```

Ao final de cada ensaio:

```bash
uv run python training/analyze_events.py --csv runs/ensaio-01.csv
```

### Ensaio de campo (um dia, fora de casa)

1. **Rede.** Wi-Fi de faculdade costuma isolar os aparelhos entre si e exigir
   login em página, que a ESP32 não faz. Use o roteador do celular ou o
   "Hotspot móvel" do Windows; ponha o IP do notebook nessa rede em
   `SERVER_HOST` (`secrets.h`) e libere a porta 8000 no firewall.
2. **Servidor gravando tudo** (PowerShell, em `server/`):

   ```powershell
   $env:AVS_STORAGE__LOG_EVENTS="true"; $env:AVS_STORAGE__SAVE_RAW_FRAMES="true"; $env:AVS_STORAGE__SAVE_ANNOTATED_FRAMES="true"
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   Confira no painel o selo `detector: yolo26... em cuda/cpu` antes de começar.
3. **Durante o ensaio:** anote hora, local e condição (corredor claro, sala,
   pouca luz, andando/parado) de cada trecho — os arquivos saem com o horário no
   nome, e é assim que se separa depois cada condição.
4. **Depois:** renomeie `var/events.jsonl` por ensaio e rode
   `analyze_events.py` (latência, quadros/s, falas por minuto). Dos quadros de
   `var/raw/`, anote 100 a 200 (Roboflow, CVAT ou Label Studio, formato YOLO) e
   rode `pipeline_ablation.py` sobre eles — é a precisão e a revocação **no seu
   ambiente**, com a sua câmera. Os de `var/snapshots/` viram as figuras.

### Do ensaio ao modelo ajustado

1. **Ensaio em duas configurações**, com a gravação de quadros crus ligada:
   (A) só COCO — o padrão — e (B) COCO + Objects365
   (`AVS_VISION__EXTRA_MODEL_PATHS=["yolo26l-objv1-150.pt"]`). Ao vivo se observa
   latência, quadros/s e comportamento da narração; a **comparação de acurácia**
   é feita depois, nas mesmas imagens anotadas (passo 5), porque duas passagens
   ao vivo nunca veem a mesma cena. Grave em **3 ou mais sessões** (locais ou
   horários diferentes, com 10 min ou mais entre elas): o teste será formado por
   sessões inteiras que o modelo nunca viu.
2. **Pré-rótulos** (na máquina com a GPU):

   ```bash
   uv run python training/build_dataset.py prelabel --images var/raw --out ../datasets/esp_pre
   ```

   Guarda 1 quadro a cada 5 s (vizinhos são quase iguais) e marca as 29 classes
   com os professores (`yolo26l` + Objects365).
3. **Revisão no CVAT** (cvat.ai, projeto privado; a equipe pode dividir as
   tarefas): crie a tarefa com as imagens de `esp_pre/images`, importe
   `cvat_yolo_1.1.zip` como *YOLO 1.1* e corrija — apague o que está errado,
   ajuste caixas e **desenhe o que falta**, principalmente degrau, meio-fio,
   escada e porta, que nenhum professor pronto conhece. Ignore objetos com menos
   de ~15 pixels. Exporte como *YOLO 1.1* **com as imagens** e salve no Drive
   como `deep-vision/esp_revisado.zip`.
4. **Treino no Colab**: abra `server/training/colab_treino.ipynb` no Colab e rode
   as células em ordem. São duas rodadas, e o notebook descobre sozinho em qual
   está: **sem** `esp_revisado.zip` no Drive, treina só com os dados públicos
   (`assistivo-publico.pt`, que já detecta escada, degrau, meio-fio, porta e
   faixa — dá para rodar antes mesmo do ensaio); **com** o zip, faz o ajuste
   fino a partir da rodada 1 (`assistivo-final.pt`). O professor das classes
   novas e o dataset montado ficam no Drive e não são refeitos.
5. **Resultado**: a célula 7 compara as três configurações — COCO, COCO +
   Objects365 e o modelo ajustado — **no mesmo teste da ESP**, com precisão,
   revocação, tipos de erro e mAP por classe. É a tabela de antes e depois do
   capítulo de resultados.
6. **No servidor**: `AVS_VISION__MODEL_PATH=assistivo-final.pt`, sem modelos extras.

Rostos de terceiros nas imagens: mantenha o projeto do CVAT e o Drive
privados e não publique o dataset sem consentimento (LGPD).

---

## 1. Tempo de inferência

**Objetivo:** sustentar a afirmação de operação em tempo real.

```bash
cd server
uv run python training/evaluate.py --benchmark-only --device cpu
uv run python training/evaluate.py --benchmark-only --device 0     # com GPU
```

Registre média, mediana, p95 e FPS estimado. Repita para
`--imgsz 320 / 480 / 640` — a tabela resultante, junto com a acurácia de cada
tamanho (seção 2), justifica a escolha de 640 como padrão: é a resolução de
treino do YOLO e a do quadro VGA da OV2640.

**Critério:** p95 abaixo de 125 ms mantém os 8 quadros/s sem descarte.

---

## 2. Acurácia da detecção

**Objetivo:** mAP, precisão e revocação por classe — e **que tipo de erro** o
sistema comete.

### 2.1 Conjunto de avaliação

Use imagens que o modelo **nunca viu no treino**. O COCO128, usado nas primeiras
medições, é um recorte do conjunto de *treino* do COCO e infla os números. Os
números de `docs/configuracao.md` vêm de 500 imagens do COCO val2017, sorteadas
com semente fixa:

```bash
# rótulos no formato YOLO (48 MB); só a pasta val2017 é usada
curl -LO https://github.com/ultralytics/assets/releases/download/v0.0.0/coco2017labels.zip
unzip -q coco2017labels.zip "coco/labels/val2017/*"
# 500 rótulos sorteados (random.Random(2026).shuffle) e as imagens de mesmo nome:
#   http://images.cocodataset.org/val2017/<nome>.jpg
# organizados como val500/images/val2017/*.jpg e val500/labels/val2017/*.txt
```

O conjunto que mais vale para o TCC, porém, é o **do próprio protótipo**:
100 a 200 quadros da ESP32-CAM no ambiente de uso, anotados (Roboflow, CVAT ou
Label Studio, exportando no formato YOLO). Mesmo 100 imagens já produzem números
defensáveis para as classes de interesse.

### 2.2 Medição

```bash
cd server
# mAP oficial (curva completa)
uv run python training/evaluate.py --weights ../models/yolo26s.pt --data caminho/data.yaml

# o sistema como implantado: JPEG da borda, pré-processamento do servidor,
# perfil de classes e limiar do servidor; erros separados por tipo
uv run python training/pipeline_ablation.py --data caminho/val500 --weights yolo26s.pt
```

O `pipeline_ablation.py` classifica cada detecção em **acerto**, **fantasma**
(nada real embaixo), **rótulo** (objeto real, nome errado), **duplicata** e
**mal localizada**, e repete a medida com pouca luz, borrão de movimento, QVGA e
CLAHE. Com `--json`, grava também precisão e revocação por classe e as confusões
mais comuns (`caminhão -> carro`, `cadeira -> sofá`...).

### 2.3 Modelo próprio (classes novas)

```bash
uv run python training/prepare_dataset.py --split --source datasets/brutas
uv run python training/train.py --epochs 100
uv run python training/evaluate.py --weights ../runs/assistivo/weights/best.pt
```

O modelo treinado só conhece as classes do dataset: ele entra **ao lado** do
modelo COCO (`AVS_VISION__EXTRA_MODEL_PATHS`), e não no lugar dele — ver
`docs/arquitetura.md`, seção 9.

Exemplo reproduzível com dados públicos, antes de ter o dataset próprio: portas,
janelas, guarda-roupas e luminárias do HomeObjects-3K.

```bash
uv run python training/prepare_homeobjects.py        # baixa 390 MB, monta datasets/interiores
uv run python training/train.py --data ../datasets/interiores/data.yaml --weights ../models/yolo26s.pt
```

**Onde treinar.** A GTX 1650 (4 GB) treina bem o `yolo26n` com o backbone
congelado: ~3,5 iterações/s com lote 16, cerca de 1 a 1,5 min por época nesse
dataset — 11× a CPU. Modelos maiores não cabem: o ultralytics desliga a precisão
mista (AMP) na série GTX 16, o treino em FP32 passa dos 4 GB e o driver
transborda para a RAM do sistema (o `yolo26s` caiu para ~3,6 s por iteração,
mais de 30 min por época). Para `s`, `m` ou `l`, use uma GPU de nuvem
(Colab ou Kaggle, 16 GB e precisão mista).

Um treino curto de demonstração (yolo26n, 6 épocas em CPU) chegou a AP50 de
42% (porta), 49% (janela), 48% (guarda-roupa) e 47% (luminária), contra 47%, 5%,
5% e 19% do YOLOE sem treino, só com o nome da classe em texto. Rodando ao lado
do `yolo26s`, a inferência em CPU passou de ~97 ms para ~135 ms por quadro.

---

## 3. Zonas de proximidade

**Objetivo:** verificar os limiares da Seção 5.4 e a estabilidade das transições.

Sem hardware:

```bash
python simulator/virtual_device.py --sonar approach
```

Acompanhe o painel: a zona deve percorrer `safe → warning → critical` em 1,50 m
e 0,80 m, e o alerta crítico deve interromper qualquer fala em curso.

Com o protótipo (bancada, fita métrica):

| Distância real | Zona esperada | Zona medida | Erro do sonar |
|---|---|---|---|
| 2,00 m | safe | | |
| 1,50 m | fronteira | | |
| 1,20 m | warning | | |
| 0,80 m | fronteira | | |
| 0,50 m | critical | | |
| 0,20 m | critical | | |

Repita cada distância 10 vezes e registre média e desvio. Aproxime e afaste o
obstáculo lentamente sobre cada fronteira: **não pode haver oscilação de zona** —
é a histerese sendo verificada.

---

## 4. Redundância local (sem rede)

**Objetivo:** comprovar a afirmação de que o alerta de colisão não depende da
rede. Exige hardware.

1. Ligue o protótipo e confirme a conexão com o servidor.
2. Desligue o servidor (`Ctrl+C`).
3. Aproxime um obstáculo até menos de 80 cm.

**Esperado:** o buzzer emite o padrão rápido normalmente. O log serial mostra as
tentativas de reconexão sem interromper a medição.

4. Religue o servidor. O dispositivo reconecta sozinho em até 3 s e a narração
   falada volta.

---

## 5. Distância estimada pela câmera

**Objetivo:** quantificar o erro da estimativa monocular.

Posicione um objeto de classe conhecida (uma cadeira, uma pessoa) a distâncias
medidas com fita métrica e compare com `distance_m` em
`GET /api/v1/media/snapshot.jpg` / no painel.

| Distância real | Estimada | Erro relativo |
|---|---|---|
| 1,0 m | | |
| 2,0 m | | |
| 3,0 m | | |
| 5,0 m | | |

Erros de 30–50% são esperados e já estão documentados como limitação. O que o
ensaio deve mostrar é que a **fusão com o sonar** corrige o valor quando o
objeto está centralizado.

---

## 6. Comandos de voz

**Objetivo:** taxa de reconhecimento de intenção.

Por texto (funciona hoje, sem microfone):

```bash
curl -s -X POST http://localhost:8000/api/v1/command \
  -H "Content-Type: application/json" \
  -d '{"text":"o que tem na minha frente?"}'
```

Percorra os doze comandos de `docs/protocolo.md` e registre acertos. A suíte
`tests/test_intents.py` já cobre as variações de escrita, acentuação e erros
comuns de transcrição.

Por voz (exige microfone): grave cada comando 10 vezes, em ambiente silencioso e
com ruído de fundo, e compare as taxas.

---

## 7. Comportamento da narração

**Objetivo:** verificar que o sistema não é verboso — o requisito de usabilidade
mais crítico do projeto.

Ensaio de 5 minutos em ambiente com movimento. Do log:

```bash
uv run python training/analyze_events.py
```

Registre:

- locuções por minuto (alvo: **abaixo de 12**);
- proporção de repetições do mesmo objeto;
- tempo entre a entrada de um objeto no quadro e o anúncio.

Se passar de 12, aumente `AVS_NARRATION__OBJECT_COOLDOWN_MS`.

---

## 8. Comparação dos transportes

**Objetivo:** dado empírico para a decisão descrita em `docs/arquitetura.md`.

```bash
python simulator/virtual_device.py --transport http --fps 8   # 60 s
python simulator/virtual_device.py --transport ws   --fps 8   # 60 s
```

Compare `avg_end_to_end_ms` e `frames_dropped` em `GET /api/v1/state` nos dois
casos.

---

## 9. Baixa luminosidade

**Objetivo:** quantificar a degradação relatada na Seção 6 do TCC.

Mesmo conjunto de imagens em três condições (bem iluminado, penumbra, escuro),
com o pré-processamento ligado e desligado:

```bash
AVS_PREPROCESS__CLAHE=false uv run uvicorn app.main:app --port 8000
AVS_PREPROCESS__CLAHE=true  uv run uvicorn app.main:app --port 8000
```

Compare o número de detecções e a confiança média. A diferença mede exatamente
o ganho do CLAHE.

---

## Registro dos resultados

Para cada ensaio, guarde:

- `server/var/events.jsonl` (renomeado por ensaio);
- o CSV de `analyze_events.py`;
- quadros anotados (`AVS_STORAGE__SAVE_ANNOTATED_FRAMES=true`), que servem
  diretamente como figuras do trabalho;
- versão do modelo, `--imgsz`, dispositivo (CPU/GPU) e commit do código.
