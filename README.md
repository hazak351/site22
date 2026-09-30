# AdFlow Studio Auto

Crie anúncios em vídeo automaticamente: descreva o anúncio, envie o produto e receba um MP4 pronto.
As cenas são geradas no **Google Flow (Veo)** usando os créditos da **sua** conta Google, operando o
Flow por um navegador Edge/Chrome automatizado (Playwright + CDP). Nenhuma API paga do Veo é usada.

```
PEDIDO → ANÁLISE DO PRODUTO → ANÁLISE DA REFERÊNCIA → DIREÇÃO CRIATIVA → PLANEJAMENTO DAS CENAS
→ PROMPTS (Product/Environment/Continuity Lock) → FLOW DOCTOR → GOOGLE FLOW → DOWNLOAD
→ QUALITY GATE → REGENERA SÓ A CENA RUIM → FFMPEG → MP4 FINAL
```

## Estado atual: o que é real e o que é simulado

| Parte | Estado |
|---|---|
| Interface web, API, projetos, estimativa e teto de créditos | ✅ real, testado |
| Análise do produto (cores, categoria, vistas) e Product Lock | ✅ real (heurística local, sem IA de visão) |
| Análise da referência (ritmo de cortes, luz, paleta, movimento) | ✅ real (FFmpeg + Pillow); movimentos específicos (orbit, dolly…) vêm do texto do briefing |
| Direção criativa, Environment Master, Continuity Lock, prompts | ✅ real, testado |
| Quality Gate técnico (duração, resolução, tela preta, congelamento, cor do produto, ambiente, continuidade) | ✅ real, testado |
| Quality Gate semântico (logo trocado, deformação, texto errado, objetos extras, aderência ao briefing) | ⚠️ **não avaliado**: exige analisador de visão (ver próximos passos). Aparece como "não avaliado", nunca como aprovado |
| Regeneração só da cena reprovada, limite de tentativas, teto de créditos | ✅ real, testado |
| Recuperação após queda (navegador, Flow, PC reiniciando) | ✅ real, testado com queda simulada |
| Editor FFmpeg (9:16, 16:9, 1:1, 30 fps, H.264/AAC, áudio, logo, CTA, música, duração exata) | ✅ real, testado |
| Automação do navegador (perfil persistente, CDP, aba própria, upload, prompt, geração, download, diagnóstico) | ✅ testada com navegador real **contra uma página falsa do Flow** |
| **Google Flow real** | ❌ **NÃO validado.** Os seletores em `config/flow_selectors.json` são candidatos. Rode o Flow Doctor na sua conta antes de gerar |
| Tabela de créditos | ⚠️ valores de exemplo em `config/credits.json`; confira no Flow |

O motor **Simulador** gera cenas sintéticas com FFmpeg para testar tudo sem gastar créditos. Ele é
sempre identificado como SIMULADOR na interface e nos logs.

## Instalação (Windows)

1. Instale o **Python** (3.11 a 3.14) em <https://www.python.org/downloads/> marcando *Add python.exe to PATH*.
2. Instale o **FFmpeg**: `winget install Gyan.FFmpeg` (ou copie `ffmpeg.exe` e `ffprobe.exe` para `tools\ffmpeg\bin`).
3. Tenha o **Microsoft Edge** ou o **Google Chrome** instalado.
4. Dê dois cliques em **`start.bat`**. Na primeira vez ele cria o `.venv` e instala as dependências
   (todas com wheels prontos, nada é compilado). Depois abre `http://127.0.0.1:8765`.

Não há dependência de PowerShell: tudo roda no CMD.

## Primeiro uso do Google Flow

1. Rode **`flow_login.bat`** (ou o botão *Flow Doctor* na interface). Abre um Edge/Chrome com um
   **perfil dedicado** do AdFlow (`data\browser-profile`), separado do seu navegador normal.
2. Faça login na sua conta Google (e 2FA) **nessa janela**. O AdFlow nunca vê nem salva sua senha;
   a sessão fica só no perfil local.
3. Rode **`flow_doctor.bat`** (ou *Flow Doctor → Rodar no Flow real*). Ele confere navegador,
   sessão, Flow, campo de prompt, upload, controles, botão de gerar, resultados e download,
   **sem gerar nada e sem gastar créditos**. Se algo falhar ele mostra a etapa, o seletor procurado,
   a URL, um screenshot e o HTML (em `data\diagnostics`). Ajuste `config/flow_selectors.json` se preciso.

## Uso

Na tela principal: descreva o anúncio, adicione fotos do produto (frente, lateral, 3/4, detalhes),
referência e logo (opcionais), escolha duração, formato e qualidade, e clique em
**CRIAR VÍDEO AUTOMATICAMENTE**.

Com o motor **Google Flow (real)** aparece uma confirmação com o plano de cenas, a estimativa e o teto
de créditos. Nada é enviado ao Flow sem essa confirmação, e o Flow Doctor roda de novo antes da
primeira geração. Durante a criação a tela mostra cada etapa, cada cena, os créditos estimados e usados,
o tempo decorrido e a etapa atual. No final, o MP4 fica disponível para baixar.

## Como funciona

- **Planejamento**: a duração é dividida no menor número de cenas possível (`24s → 8+8+8`,
  `20s → 7+7+6`). Cada cena é gerada com a duração que o Flow oferece (`generation_durations`) e o
  editor corta o excesso. Sobra de clipe vira transição suave; sem sobra, corte seco, e a duração
  final continua exata.
- **Product Lock**: em todas as cenas, instruções de forma, proporção, cores detectadas e citadas,
  acabamento, detalhes, logo e "nunca virar AirPods/produto genérico". As fotos do produto são
  enviadas como referência.
- **Environment Master**: cenário, superfície, fundo, direção e temperatura da luz, sombras,
  profundidade, paleta, atmosfera e materiais, idênticos em todas as cenas. O frame central da
  primeira cena aprovada vira a imagem de referência do ambiente.
- **Continuity Lock**: o estado final da cena N é o estado inicial da cena N+1, e o último frame
  da cena N é usado como primeiro frame da N+1 (modo *Frames to Video*, configurável).
- **Quality Gate**: se uma cena reprova, só ela é regenerada, com correções no prompt, até o limite
  de tentativas e sem passar do teto de créditos. Esgotado o limite, usa a melhor tentativa e avisa.
- **Logo, textos e CTA** entram na pós-produção, nunca gerados pela IA.
- **Recuperação**: tudo fica em `data\projects\<id>\project.json`. Se o PC reiniciar ou o navegador
  fechar, abra o projeto e clique em *Retomar*: etapas concluídas e cenas aprovadas são mantidas, e
  um download completo antes da queda é reaproveitado sem gastar créditos de novo.
- **Logs**: `logs\<id>\project.log` (legível) e `events.jsonl` (estruturado), com prompts, cenas,
  tentativas, Flow, downloads, FFmpeg e erros. Senhas, cookies e tokens são mascarados.

## Estrutura

```
adflow/            backend (FastAPI) e pipeline
  analysis.py      análise do produto e da referência + Product Lock
  director.py      direção criativa, Environment Master, cenas e continuidade
  prompts.py       prompts com os três locks
  quality.py       Quality Gate
  editor.py        montagem FFmpeg
  pipeline.py      orquestração, créditos e recuperação
  flow/            simulador, driver real (Playwright/CDP), seletores e Flow Doctor
config/            credits.json, flow_selectors.json, settings.example.json
frontend/          interface web (HTML/CSS/JS, sem build)
tests/             testes automatizados (inclui uma página FALSA do Flow)
legacy/            página antiga do repositório (Modaswell)
start.bat          instala e inicia
flow_login.bat     abre o navegador de automação para login
flow_doctor.bat    diagnóstico do Flow real, sem gastar créditos
run_tests.bat      roda os testes
```

## Configuração

Copie `config/settings.example.json` para `config/settings.json` e altere só o necessário
(caminho do navegador, porta CDP, durações que o Flow gera, timeout, etc.).
Os créditos por modo ficam em `config/credits.json`.

## Testes

`run_tests.bat` (Windows) ou `python -m pytest -q`. Os testes cobrem planejamento, créditos, os
três locks, prompts, Quality Gate, editor (três formatos, áudio, duração, logo, CTA, música,
transições), pipeline completo, regeneração, teto, recuperação após queda, API, driver Playwright
com navegador de verdade contra a página falsa do Flow, redação de segredos, CRLF dos `.bat` e
sintaxe do JavaScript.

## Próximos passos

1. **Validar no Flow real**: rodar o Flow Doctor na conta do usuário e ajustar
   `config/flow_selectors.json`; depois, uma geração curta (1 cena, modo econômico) com confirmação.
2. **Analisador de visão** opcional para o Quality Gate semântico e para identificar movimentos de
   câmera na referência (a interface `VisionJudge` já existe em `quality.py`).
3. **Meus Produtos e Brand Kit**: salvar produtos (fotos, cores, Product Lock) e marca (logo, cores,
   tipografia, CTA, música, Environment Master) para reutilizar.
4. Recuperar do próprio Flow um vídeo gerado antes de uma queda (hoje a tentativa interrompida é
   contada como gasta, por segurança).
