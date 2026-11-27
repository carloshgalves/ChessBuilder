# Chess Builder

**Conheça seu jogo. Treine com propósito. Evolua.**

Plataforma local de análise e treinamento de xadrez baseada no histórico de partidas do jogador. O Chess Builder conecta análise computacional, diagnóstico de competências e prática personalizada em um ciclo contínuo de aprendizagem.

> Status: em planejamento. A implementação será acompanhada nas [issues do projeto](https://github.com/carloshgalves/ChessBuilder/issues).

## Funcionalidades planejadas

| Módulo | Funcionalidades |
| --- | --- |
| Histórico | Sincronização Chess.com e Lichess, importação PGN, rating, evolução e retrospectiva |
| Análise | Stockfish, precisão, variantes, momentos críticos e revisão interativa |
| Diagnóstico | Temas táticos, erros estratégicos, competências por fase e prioridades de estudo |
| Treinamento | Exercícios pessoais, coleções temáticas, método Woodpecker e repetição espaçada |
| Aberturas | Repertórios, variantes, transposições, armadilhas e revisão |
| Finais | Posições teóricas, tablebases, defesa e conversão |
| Biblioteca | Partidas históricas, posições de estudo e importação de materiais particulares |
| Bots | Força calibrável, estilos de jogo e adversários personalizados |
| Torneios | Preparação, simulações e avaliação pós-competição |

## Ciclo de aprendizagem

```mermaid
flowchart LR
    A[Partidas] --> B[Análise]
    B --> C[Diagnóstico]
    C --> D[Plano de treino]
    D --> E[Prática]
    E --> F[Acompanhamento]
    F --> C
```

O diagnóstico considera oportunidades, frequência, gravidade e recorrência dos erros. Os exercícios preservam a origem da posição e as variantes relevantes. O progresso é medido tanto nas sessões de estudo quanto nas partidas posteriores.

## Arquitetura proposta

```mermaid
flowchart TD
    UI[React + TypeScript] --> API[FastAPI]
    API --> DB[(SQLite)]
    API --> Q[Fila local persistente]
    Q --> SF[Stockfish UCI]
    Q --> DX[Diagnóstico enxadrístico]
    SF --> DX
    DX --> TR[Motor de treinamento]
    TR --> DB
    API --> SRC[Chess.com / Lichess / PGN]
    SF --> TB[Syzygy]
```

| Componente | Tecnologia |
| --- | --- |
| Interface | React, TypeScript e Vite |
| API local | Python e FastAPI |
| Regras e notação | python-chess |
| Análise | Stockfish / UCI |
| Persistência | SQLite, migrações e WAL |
| Finais | Syzygy |
| Testes | Pytest, Vitest e Playwright |

A aplicação será single-user, executada localmente, com análise progressiva, cache, retomada de jobs e limites configuráveis de recursos. O diagnóstico e o treinamento funcionam sem serviços generativos.

## Roadmap

| Fase | Entrega | Prioridade |
| --- | --- | --- |
| F0 | [Arquitetura e banco](../../issues/1) | P0 |
| F1 | [Importação de partidas](../../issues/2) | P0 |
| F2 | [Motor de análise e processamento](../../issues/3) | P0 |
| F3 | [Biblioteca e revisão de partidas](../../issues/4) | P0 |
| F4 | [Dashboard e retrospectiva](../../issues/5) | P0 |
| F5 | [Classificação tática](../../issues/6) | P0 |
| F6 | [Exercícios pessoais](../../issues/7) | P0 |
| F7 | [Competências e treinador diário](../../issues/8) | P0 |
| F8 | [Finais e tablebases](../../issues/9) | P1 |
| F9 | [Repertório de aberturas](../../issues/10) | P1 |
| F10 | [Woodpecker e repetição espaçada](../../issues/11) | P1 |
| F11 | [Biblioteca histórica e estratégia](../../issues/12) | P1 |
| F12 | [Importação de exercícios de livros](../../issues/13) | P1 |
| F13 | [Diagnóstico estratégico avançado](../../issues/14) | P1 |
| F14 | [Bots e calibração](../../issues/15) | P1 |
| F15 | [Preparação para torneios](../../issues/16) | P2 |
| F16 | [Extensão opcional de explicações](../../issues/17) | P2 |

## Fontes e ferramentas

- [Chess.com Published Data API](https://www.chess.com/news/view/published-data-api)
- [Lichess API](https://lichess.org/api) e [base aberta de exercícios](https://database.lichess.org/)
- [Stockfish](https://stockfishchess.org/)
- [python-chess](https://python-chess.readthedocs.io/)
- [PGN Mentor](https://www.pgnmentor.com/files.html)

## Dados e distribuição

Partidas importadas, históricos pessoais, PDFs, coleções particulares, avaliações e arquivos de tablebases permanecem no diretório local de dados. Materiais de terceiros mantêm sua proveniência e condições de uso.

O Chess Builder é destinado a estudo, análise pós-partida e preparação prévia para competições.

## Desenvolvimento local

Requisitos: Python 3.11+, Node.js 22+, npm. A partir da raiz:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
uvicorn chessbuilder.main:app --host 127.0.0.1 --port 8000
```

Em outro terminal:

```sh
cd frontend
npm install
npm run dev
```

Abra http://127.0.0.1:5173. Os dados ficam em `~/.local/share/chessbuilder` (override: `CHESSBUILDER_DATA_DIR`). Execute `pytest` e, no frontend, `npm run build` e `npm test`.
