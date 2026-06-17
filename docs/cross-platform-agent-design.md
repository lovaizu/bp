# CC/GHC クロスプラットフォーム エージェント設計

## 1. 目的

Claude Code（CC）で開発・安定化したエージェントシステムを、マッピングルールに基づくスクリプトでGitHub Copilot（GHC）向けに機械変換する。仕様追随しやすいよう、変換ルールはマッピングテーブルとして管理する。

本ドキュメントでは設計仕様に加え、「BLACKPINKセットリスト・プランナー」を具体例として全ファイルの実装を掲載する。後日CC/GHCの両環境で実際に動作確認を行い、設計の妥当性を裏取りする。


## 2. アーキテクチャ概要

```
ユーザー
  │
  ▼
┌──────────────────────────────────────────┐
│  エントリポイント（委譲するだけ）           │
│  CC: スラッシュコマンド (.claude/commands/) │
│  GHC: プロンプト (.github/prompts/)        │
└──────────┬───────────────────────────────┘
           │ スキルに委譲
           ▼
┌──────────────────────────────────────────┐
│  スキル（SKILL.md）                        │
│  CC: .claude/skills/<name>/SKILL.md       │
│  GHC: .github/skills/<name>/SKILL.md      │
│                                           │
│  WFを選択・起動する振り分け役              │
└──────────┬───────────────────────────────┘
           │ WFを実行
           ▼
┌──────────────────────────────────────────┐
│  ワークフロー（WF）                        │
│  スキルフォルダ内のMDファイル群             │
│  ・ユーザーインタラクション（質問・確認）    │
│  ・全体フロー制御                          │
│  ・サブエージェント呼び出し                 │
└──────────┬───────────────────────────────┘
           │ 専門タスクを委譲
           ▼
┌──────────────────────────────────────────┐
│  サブエージェント                           │
│  CC: .claude/agents/ (Taskツール)          │
│  GHC: .github/agents/ (runSubagent)        │
│  ・独立コンテキスト                         │
│  ・単一責務・JSON I/O・ツール最小限         │
└──────────────────────────────────────────┘
```


## 3. レイヤー間 I/O 仕様

全レイヤー間のデータ受け渡しはLLMのプロンプト解釈を経由する。確定的な関数呼び出しではない。
I/O 設計の原則は「LLMが確実に解釈できる自然言語の作業指示」とする。

### 3.1 エントリポイント → スキル

自然言語。ユーザーの入力テキストをそのままスキルに渡す。構造化不要。

### 3.2 スキル（SKILL.md） → ワークフロー

自然言語による作業指示。「このファイルを読み込み、その手順に従って作業してください」の形式。
ユーザーのクエリがそのままコンテキストとして流れる。

### 3.3 ワークフロー → サブエージェント（核心）

自然言語で明示的にサブエージェント名を指定し、依頼内容を箇条書きで渡す。
サブエージェントは独立コンテキストで起動するため、必要な情報はすべてプロンプトに含める。

### 3.4 サブエージェントの出力

サブエージェントの出力だけJSON形式を指定する。
親が次のステップで機械的に利用するため、形式の安定が重要。
JSON以外のテキスト（説明文、前置き、補足）を含めないことをシステムプロンプトで明示する。

CC公式ドキュメント：
> 「The parent receives the subagent's final message verbatim as the Agent tool result」

### 3.5 エラー：共通フォーマット

全サブエージェント共通のエラーJSON形式を定める。

```json
{
  "status": "error",
  "agent": "<サブエージェント名>",
  "message": "<エラー詳細>"
}
```

### 3.6 サブエージェントのネスト禁止

- CC公式: 「subagents cannot spawn other subagents」（built-in制約）
- GHC: ネストは設定で有効化可能だが、深さ1階層が実用限界
- 設計方針: WFがオーケストレータ。サブエージェント同士は直接通信しない


## 4. 具体例：BLACKPINKセットリスト・プランナー

ユーザーがライブのテーマを伝えると、楽曲を検索し、セットリストの流れを評価し、演出プランを生成する。

### 4.1 ディレクトリ構成（CC版）

```
.claude/
├── commands/
│   └── bp.md                              ← /bp <テーマ>
├── skills/
│   └── blackpink/
│       ├── SKILL.md                       ← WF振り分け
│       ├── workflows/
│       │   ├── quick.md                   ← WF1: finder → planner
│       │   ├── optimized.md               ← WF2: finder → evaluator → planner
│       │   └── versus.md                  ← WF3: finder×2 → evaluator → planner
│       └── resources/
│           ├── songs.json                 ← 楽曲DB（25曲）
│           ├── members.json               ← メンバー情報
│           └── filter-songs.sh            ← BPM/タグフィルタスクリプト
└── agents/
    ├── bp-song-finder.md                  ← サブエージェント1: 楽曲検索
    ├── bp-setlist-evaluator.md            ← サブエージェント2: 曲順評価
    └── bp-show-planner.md                 ← サブエージェント3: 演出プラン生成
```

### 4.2 データフロー

```
WF: quick        finder ──────────────────────────→ planner
WF: optimized    finder ──→ evaluator ──→ planner
WF: versus       finder(A) ─┐
                             ├→ evaluator ──→ planner
                 finder(B) ─┘
```

### 4.3 JSON受け渡しの流れ（optimized の場合）

```
[WF] テーマ「前半エモ後半アゲ」
  │
  │  依頼内容を自然言語で渡す
  ▼
[bp-song-finder]
  │  IN:  テーマ、songs.json パス、filter-songs.sh パス
  │  OUT: {"status":"ok", "songs":[...], "song_count":8, "theme_summary":"..."}
  ▼
[WF] finder の結果JSONをそのまま次のサブエージェントに渡す
  │
  ▼
[bp-setlist-evaluator]
  │  IN:  finder の結果JSON + members.json パス
  │  OUT: {"status":"ok", "setlist_order":[...], "flow_score":85, "warnings":[...]}
  ▼
[WF] evaluator の結果JSONをそのまま次のサブエージェントに渡す
  │
  ▼
[bp-show-planner]
  │  IN:  evaluator の結果JSON + テーマ + members.json パス
  │  OUT: {"status":"ok", "show_plan":{...}}
  ▼
[WF] show_plan をユーザーに整形して表示
```


### 4.4 全ファイル実装（CC版）

---

#### `.claude/commands/bp.md`

```markdown
---
description: BLACKPINK setlist planner. Generate a concert setlist based on a theme.
command: /bp
---
Use the blackpink skill to create a setlist for the following theme.

Theme: $ARGUMENTS
```

---

#### `.claude/skills/blackpink/SKILL.md`

```markdown
---
name: blackpink
description: >
  BLACKPINK concert setlist planner.
  Use when the user asks to create a setlist, plan a concert,
  pick songs for a live show, or anything related to BLACKPINK performance planning.
---

# BLACKPINK Setlist Planner

You are a concert setlist planner for BLACKPINK.
Based on the user's request, select and execute one of the following workflows.

## Resources

This skill folder contains the following resources under `resources/`:
- `songs.json` — Full discography with BPM, energy, mood tags, and member info
- `members.json` — Member profiles with solo songs and performance strengths
- `filter-songs.sh` — Helper script to filter songs by mood, energy, BPM, member, or role

The path to this skill folder is `.claude/skills/blackpink/`.
All subagents should reference resources using this base path.

## Workflow Selection

Determine which workflow to use based on the user's request:

- **Quick mode** — The user wants a fast, simple setlist without detailed optimization.
  Keywords: "quick", "simple", "just give me", or short/casual requests.
  → Read and follow: `workflows/quick.md`

- **Optimized mode** — The user wants a polished setlist with flow analysis.
  Keywords: "optimized", "best flow", "perfect", or requests mentioning transitions, energy flow, or pacing.
  → Read and follow: `workflows/optimized.md`

- **Versus mode** — The user provides two themes or concepts to compare.
  Keywords: "compare", "versus", "vs", "A or B", or two distinct themes mentioned.
  → Read and follow: `workflows/versus.md`

If the mode is ambiguous, default to **optimized**.
```

---

#### `.claude/skills/blackpink/workflows/quick.md`

```markdown
# Workflow: Quick Setlist

Fast setlist generation. Search songs, then directly generate a show plan.
Skips the evaluation/optimization step.

Flow: bp-song-finder → bp-show-planner

## Step 1: Song Search

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (the user's theme)
- songs.json path: .claude/skills/blackpink/resources/songs.json
- filter-songs.sh path: .claude/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 8

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: (the user's theme)
- Song data: (the full JSON result from Step 1)
- members.json path: .claude/skills/blackpink/resources/members.json
- Optimization level: basic

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Present to User

Format the show plan from Step 2 into a readable setlist for the user.
Include: song order, estimated duration, and stage notes.
```

---

#### `.claude/skills/blackpink/workflows/optimized.md`

```markdown
# Workflow: Optimized Setlist

Full pipeline with flow evaluation. Search songs, evaluate the order,
then generate a polished show plan.

Flow: bp-song-finder → bp-setlist-evaluator → bp-show-planner

## Step 1: Song Search

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (the user's theme)
- songs.json path: .claude/skills/blackpink/resources/songs.json
- filter-songs.sh path: .claude/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 12

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Setlist Evaluation

Delegate to the bp-setlist-evaluator subagent with the following instructions.

Request:
- Song search result: (the full JSON result from Step 1)
- members.json path: .claude/skills/blackpink/resources/members.json
- Target setlist length: 8-10 songs
- Evaluation criteria: energy flow, mood transitions, solo balance, dance break spacing

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: (the user's theme)
- Evaluated setlist: (the full JSON result from Step 2)
- members.json path: .claude/skills/blackpink/resources/members.json
- Optimization level: full

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 4: Present to User

Format the show plan from Step 3 into a detailed setlist for the user.
Include: song order with position rationale, energy curve description,
estimated total duration, stage/lighting notes, and member highlights.
If the evaluator noted any warnings, mention them as suggestions.
```

---

#### `.claude/skills/blackpink/workflows/versus.md`

```markdown
# Workflow: Versus Setlist

Compare two themes by running song search twice, merging results,
evaluating them together, then producing a comparative show plan.

Flow: bp-song-finder(A) + bp-song-finder(B) → bp-setlist-evaluator → bp-show-planner

The user will provide two themes (e.g., "fierce vs emotional").
Parse the two themes from the user's input. If only one theme is found, ask the user
to provide a second theme before proceeding.

## Step 1A: Song Search for Theme A

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (first theme)
- songs.json path: .claude/skills/blackpink/resources/songs.json
- filter-songs.sh path: .claude/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 10

Keep the full JSON result as `result_a`.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 1B: Song Search for Theme B

Delegate to the bp-song-finder subagent with the following instructions.

Request:
- Theme: (second theme)
- songs.json path: .claude/skills/blackpink/resources/songs.json
- filter-songs.sh path: .claude/skills/blackpink/resources/filter-songs.sh
- Max songs to return: 10

Keep the full JSON result as `result_b`.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 2: Comparative Evaluation

Delegate to the bp-setlist-evaluator subagent with the following instructions.

Request:
- Song search results: provide BOTH result_a and result_b as follows:
  - theme_a: { name: "(first theme)", search_result: (result_a) }
  - theme_b: { name: "(second theme)", search_result: (result_b) }
- members.json path: .claude/skills/blackpink/resources/members.json
- Target setlist length: 8-10 songs per theme
- Evaluation criteria: energy flow, mood transitions, solo balance, dance break spacing
- Mode: comparative (evaluate both and recommend which theme produces the better setlist)

Do not modify the subagent's result. Keep the full JSON as-is for the next step.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 3: Show Plan Generation

Delegate to the bp-show-planner subagent with the following instructions.

Request:
- Theme: provide both theme names and indicate which was recommended by the evaluator
- Evaluated setlist: (the full JSON result from Step 2, which contains both plans)
- members.json path: .claude/skills/blackpink/resources/members.json
- Optimization level: full
- Mode: comparative (generate plans for both and highlight differences)

Do not modify the subagent's result. Keep the full JSON as-is.

If the result contains `"status": "error"`, show the error to the user and stop.

## Step 4: Present to User

Format both show plans side by side for comparison.
Include: which theme the evaluator recommended and why,
song order for each, energy curves, total durations, and key differences.
Let the user choose which plan to go with.
```

---

#### `.claude/agents/bp-song-finder.md`

```markdown
---
name: bp-song-finder
description: >
  Searches BLACKPINK songs by theme, mood, energy, or member.
  Use when a workflow needs to find songs matching specific criteria.
  Returns a JSON array of matching songs with metadata.
tools: Read, Grep, Glob, Bash
model: haiku
---

# Role

You are a BLACKPINK discography search specialist.
You receive a theme or search criteria and return matching songs from the song database.

# Input

You will receive the following from the parent workflow:
- Theme: a natural language description of the desired vibe/concept
- songs.json path: path to the song database file
- filter-songs.sh path: path to the filtering helper script
- Max songs to return: maximum number of songs in the result

# Procedure

1. Read songs.json to understand the full catalog.
2. Analyze the theme and determine which mood tags, energy ranges, and song roles are relevant.
3. Use filter-songs.sh to narrow down candidates. You can run it multiple times with different filters.
   Usage: bash <filter-songs.sh path> <songs.json path> <filter_type> <filter_value>
   Filter types: mood <tag>, energy <min> <max>, member <id>, suitable <role>, bpm <min> <max>
4. From the filtered results, select the best matches for the theme.
5. Return the result in the output format below.

# Output Format

Return ONLY the following JSON. No other text before or after.

{
  "status": "ok",
  "theme_input": "the original theme text",
  "theme_summary": "your 1-sentence interpretation of the theme",
  "song_count": <number of songs returned>,
  "songs": [
    {
      "id": "song_id",
      "title": "Song Title",
      "bpm": 130,
      "energy": 90,
      "mood": ["tag1", "tag2"],
      "duration_sec": 200,
      "members_featured": ["member1", "member2"],
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"],
      "match_reason": "why this song fits the theme"
    }
  ]
}

# Error Format

If anything fails (file not found, script error, no matching songs), return:

{
  "status": "error",
  "agent": "bp-song-finder",
  "message": "description of what went wrong"
}
```

---

#### `.claude/agents/bp-setlist-evaluator.md`

```markdown
---
name: bp-setlist-evaluator
description: >
  Evaluates and optimizes a BLACKPINK setlist order.
  Analyzes energy flow, mood transitions, solo balance, and dance break spacing.
  Use when a workflow needs quality evaluation of a song selection.
tools: Read, Grep, Glob
model: sonnet
---

# Role

You are a concert setlist flow analyst for BLACKPINK.
You receive a set of candidate songs and evaluate/optimize their order
for the best concert experience.

# Input

You will receive one of the following input patterns:

**Single theme (from optimized workflow):**
- Song search result: JSON from bp-song-finder (contains .songs array)
- members.json path: path to member profiles
- Target setlist length: desired number of songs
- Evaluation criteria: what to optimize for

**Two themes (from versus workflow):**
- Song search results: two sets labeled theme_a and theme_b, each containing:
  - name: theme name
  - search_result: JSON from bp-song-finder
- members.json path: path to member profiles
- Target setlist length: desired number of songs per theme
- Evaluation criteria: what to optimize for
- Mode: "comparative"

# Procedure

1. Read members.json to understand member strengths and solo repertoire.
2. From the candidate songs, select the target number that best covers the criteria.
3. Arrange the selected songs into an optimal order considering:
   - Energy curve: build → peak → cooldown → build → final peak pattern
   - BPM transitions: avoid jarring BPM jumps (>40 BPM between consecutive songs)
   - Mood flow: smooth transitions between contrasting moods
   - Solo balance: distribute solo/featured songs evenly across the set
   - Dance break spacing: avoid consecutive dance-heavy songs (performer fatigue)
4. Score the overall flow quality (0-100).
5. Note any warnings (e.g., "3 high-energy songs in a row at positions 4-6").
6. For comparative mode: evaluate both themes independently, then recommend one.

# Output Format — Single Theme

Return ONLY the following JSON. No other text.

{
  "status": "ok",
  "mode": "single",
  "setlist_order": [
    {
      "position": 1,
      "song_id": "song_id",
      "title": "Song Title",
      "role": "opener",
      "energy": 90,
      "bpm": 130,
      "transition_note": "opens with high energy to grab attention"
    }
  ],
  "total_duration_sec": 1800,
  "flow_score": 85,
  "energy_curve": "high → sustained → dip → build → peak → emotional close",
  "solo_distribution": {
    "jisoo": 1,
    "jennie": 1,
    "rose": 1,
    "lisa": 1
  },
  "warnings": [
    "3 consecutive songs above 85 energy at positions 2-4"
  ],
  "optimization_notes": "summary of key ordering decisions"
}

# Output Format — Comparative

{
  "status": "ok",
  "mode": "comparative",
  "theme_a": {
    "name": "theme A name",
    "setlist_order": [ ... same structure as single ... ],
    "total_duration_sec": 1800,
    "flow_score": 85,
    "energy_curve": "...",
    "solo_distribution": { ... },
    "warnings": [ ... ]
  },
  "theme_b": {
    "name": "theme B name",
    "setlist_order": [ ... ],
    "total_duration_sec": 1750,
    "flow_score": 78,
    "energy_curve": "...",
    "solo_distribution": { ... },
    "warnings": [ ... ]
  },
  "recommendation": "a" or "b",
  "recommendation_reason": "Theme A scores higher because..."
}

# Error Format

{
  "status": "error",
  "agent": "bp-setlist-evaluator",
  "message": "description of what went wrong"
}
```

---

#### `.claude/agents/bp-show-planner.md`

```markdown
---
name: bp-show-planner
description: >
  Generates a complete BLACKPINK show plan with stage directions and production notes.
  Use when a workflow has a finalized setlist and needs the full performance plan.
tools: Read, Grep, Glob
model: sonnet
---

# Role

You are a BLACKPINK concert production planner.
You receive an evaluated setlist and produce a complete show plan
with staging, lighting cues, and member choreography highlights.

# Input

You will receive the following from the parent workflow:
- Theme: the original theme(s) from the user
- Evaluated setlist: JSON from bp-setlist-evaluator (contains setlist_order)
- members.json path: path to member profiles
- Optimization level: "basic" (quick workflow) or "full" (optimized/versus workflow)
- Mode (optional): "comparative" if from versus workflow

# Procedure

1. Read members.json to understand each member's performance strengths.
2. For each song in the setlist order:
   - Determine stage layout (full stage / center stage / runway / sub-stages)
   - Assign lighting mood (color palette, intensity)
   - Note choreography highlights based on member strengths
   - Plan transitions between songs (video interlude, outfit change, talk segment)
3. For "basic" optimization: keep notes brief, 1-2 lines per song.
4. For "full" optimization: include detailed production notes per song.
5. For "comparative" mode: generate plans for both themes.
6. Calculate total show duration including transitions.

# Output Format — Single Theme

Return ONLY the following JSON. No other text.

{
  "status": "ok",
  "mode": "single",
  "theme": "the theme",
  "show_plan": {
    "total_duration_min": 75,
    "song_count": 8,
    "sections": [
      {
        "section_name": "Opening",
        "songs": [
          {
            "position": 1,
            "song_id": "how_you_like_that",
            "title": "How You Like That",
            "stage_layout": "full_stage",
            "lighting": {
              "color_palette": ["red", "white"],
              "intensity": "high",
              "effects": ["strobes", "pyro"]
            },
            "choreography_highlight": "Full group formation with Lisa center for dance break",
            "member_moments": [
              {"member": "lisa", "moment": "Dance break solo center stage"},
              {"member": "jennie", "moment": "Rap verse with spotlight"}
            ],
            "transition_to_next": {
              "type": "video_interlude",
              "duration_sec": 30,
              "description": "Countdown visual with bass buildup"
            }
          }
        ]
      }
    ],
    "production_summary": "overall description of the show's arc and feel"
  }
}

# Output Format — Comparative

{
  "status": "ok",
  "mode": "comparative",
  "recommended_theme": "a" or "b",
  "plan_a": { ... same structure as single ... },
  "plan_b": { ... same structure as single ... },
  "comparison_summary": "Key differences and why one works better"
}

# Error Format

{
  "status": "error",
  "agent": "bp-show-planner",
  "message": "description of what went wrong"
}
```

---

#### `.claude/skills/blackpink/resources/songs.json`

```json
{
  "songs": [
    {
      "id": "boombayah",
      "title": "BOOMBAYAH",
      "album": "SQUARE ONE",
      "year": 2016,
      "bpm": 175,
      "duration_sec": 223,
      "mood": ["hype", "powerful", "party"],
      "energy": 95,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "whistle",
      "title": "Whistle",
      "album": "SQUARE ONE",
      "year": 2016,
      "bpm": 103,
      "duration_sec": 207,
      "mood": ["chill", "dreamy", "smooth"],
      "energy": 50,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["cooldown", "transition"]
    },
    {
      "id": "playing_with_fire",
      "title": "Playing with Fire",
      "album": "SQUARE TWO",
      "year": 2016,
      "bpm": 120,
      "duration_sec": 209,
      "mood": ["emotional", "intense", "yearning"],
      "energy": 65,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["build", "emotional_peak"]
    },
    {
      "id": "stay",
      "title": "Stay",
      "album": "SQUARE TWO",
      "year": 2016,
      "bpm": 106,
      "duration_sec": 212,
      "mood": ["warm", "acoustic", "heartfelt"],
      "energy": 35,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["cooldown", "ballad_section"]
    },
    {
      "id": "as_if_its_your_last",
      "title": "As If It's Your Last",
      "album": "As If It's Your Last",
      "year": 2017,
      "bpm": 130,
      "duration_sec": 215,
      "mood": ["fun", "bright", "energetic"],
      "energy": 80,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["encore", "peak"]
    },
    {
      "id": "ddu_du_ddu_du",
      "title": "DDU-DU DDU-DU",
      "album": "SQUARE UP",
      "year": 2018,
      "bpm": 98,
      "duration_sec": 209,
      "mood": ["fierce", "iconic", "powerful"],
      "energy": 90,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "forever_young",
      "title": "Forever Young",
      "album": "SQUARE UP",
      "year": 2018,
      "bpm": 123,
      "duration_sec": 221,
      "mood": ["uplifting", "youthful", "bright"],
      "energy": 75,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["build", "encore"]
    },
    {
      "id": "kill_this_love",
      "title": "Kill This Love",
      "album": "KILL THIS LOVE",
      "year": 2019,
      "bpm": 130,
      "duration_sec": 190,
      "mood": ["dramatic", "fierce", "powerful"],
      "energy": 92,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "dont_know_what_to_do",
      "title": "Don't Know What to Do",
      "album": "KILL THIS LOVE",
      "year": 2019,
      "bpm": 118,
      "duration_sec": 208,
      "mood": ["emotional", "conflicted", "dramatic"],
      "energy": 60,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["build", "transition"]
    },
    {
      "id": "how_you_like_that",
      "title": "How You Like That",
      "album": "THE ALBUM",
      "year": 2020,
      "bpm": 130,
      "duration_sec": 183,
      "mood": ["triumphant", "fierce", "powerful"],
      "energy": 93,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "pretty_savage",
      "title": "Pretty Savage",
      "album": "THE ALBUM",
      "year": 2020,
      "bpm": 128,
      "duration_sec": 198,
      "mood": ["confident", "fierce", "glamorous"],
      "energy": 85,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["peak", "build"]
    },
    {
      "id": "lovesick_girls",
      "title": "Lovesick Girls",
      "album": "THE ALBUM",
      "year": 2020,
      "bpm": 128,
      "duration_sec": 199,
      "mood": ["emotional", "anthemic", "uplifting"],
      "energy": 72,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["encore", "emotional_peak"]
    },
    {
      "id": "ice_cream",
      "title": "Ice Cream (with Selena Gomez)",
      "album": "THE ALBUM",
      "year": 2020,
      "bpm": 113,
      "duration_sec": 177,
      "mood": ["fun", "playful", "sweet"],
      "energy": 60,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["cooldown", "transition"]
    },
    {
      "id": "lalisa",
      "title": "LALISA",
      "album": "LALISA",
      "year": 2021,
      "bpm": 130,
      "duration_sec": 196,
      "mood": ["fierce", "powerful", "cultural"],
      "energy": 90,
      "members_featured": ["lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["solo_stage", "peak"]
    },
    {
      "id": "money",
      "title": "Money",
      "album": "LALISA",
      "year": 2021,
      "bpm": 120,
      "duration_sec": 169,
      "mood": ["confident", "hype", "party"],
      "energy": 88,
      "members_featured": ["lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["solo_stage", "peak"]
    },
    {
      "id": "solo",
      "title": "SOLO",
      "album": "SOLO",
      "year": 2018,
      "bpm": 130,
      "duration_sec": 193,
      "mood": ["empowering", "confident", "dramatic"],
      "energy": 82,
      "members_featured": ["jennie"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["solo_stage", "peak"]
    },
    {
      "id": "on_the_ground",
      "title": "On The Ground",
      "album": "R",
      "year": 2021,
      "bpm": 160,
      "duration_sec": 185,
      "mood": ["reflective", "anthemic", "uplifting"],
      "energy": 68,
      "members_featured": ["rose"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["solo_stage", "emotional_peak"]
    },
    {
      "id": "gone",
      "title": "Gone",
      "album": "R",
      "year": 2021,
      "bpm": 80,
      "duration_sec": 214,
      "mood": ["sad", "vulnerable", "emotional"],
      "energy": 30,
      "members_featured": ["rose"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["ballad_section", "cooldown"]
    },
    {
      "id": "flower",
      "title": "FLOWER",
      "album": "ME",
      "year": 2023,
      "bpm": 120,
      "duration_sec": 185,
      "mood": ["elegant", "powerful", "graceful"],
      "energy": 78,
      "members_featured": ["jisoo"],
      "has_rap": false,
      "has_dance_break": true,
      "suitable_for": ["solo_stage", "build"]
    },
    {
      "id": "shut_down",
      "title": "Shut Down",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 130,
      "duration_sec": 179,
      "mood": ["fierce", "classical", "dramatic"],
      "energy": 88,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "pink_venom",
      "title": "Pink Venom",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 130,
      "duration_sec": 187,
      "mood": ["fierce", "cultural", "powerful"],
      "energy": 91,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": true,
      "suitable_for": ["opener", "peak"]
    },
    {
      "id": "typa_girl",
      "title": "Typa Girl",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 105,
      "duration_sec": 178,
      "mood": ["confident", "playful", "glamorous"],
      "energy": 70,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["transition", "build"]
    },
    {
      "id": "tally",
      "title": "Tally",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 112,
      "duration_sec": 188,
      "mood": ["rebellious", "fun", "carefree"],
      "energy": 72,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": true,
      "has_dance_break": false,
      "suitable_for": ["build", "transition"]
    },
    {
      "id": "hard_to_love",
      "title": "Hard to Love",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 125,
      "duration_sec": 172,
      "mood": ["emotional", "synthy", "dreamy"],
      "energy": 55,
      "members_featured": ["rose"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["cooldown", "transition"]
    },
    {
      "id": "yeah_yeah_yeah",
      "title": "Yeah Yeah Yeah",
      "album": "BORN PINK",
      "year": 2022,
      "bpm": 130,
      "duration_sec": 191,
      "mood": ["uplifting", "emotional", "anthemic"],
      "energy": 70,
      "members_featured": ["jisoo", "jennie", "rose", "lisa"],
      "has_rap": false,
      "has_dance_break": false,
      "suitable_for": ["encore", "emotional_peak"]
    }
  ]
}
```

---

#### `.claude/skills/blackpink/resources/members.json`

```json
{
  "members": [
    {
      "id": "jisoo",
      "name": "Jisoo",
      "full_name": "Kim Ji-soo",
      "role": ["vocalist", "visual"],
      "solo_songs": ["flower"],
      "strengths": ["stable_vocals", "stage_presence", "elegance"],
      "performance_style": "graceful and expressive",
      "vocal_range": "mezzo-soprano"
    },
    {
      "id": "jennie",
      "name": "Jennie",
      "full_name": "Jennie Kim",
      "role": ["rapper", "vocalist"],
      "solo_songs": ["solo"],
      "strengths": ["rap_flow", "charisma", "versatility"],
      "performance_style": "fierce and commanding",
      "vocal_range": "mezzo-soprano"
    },
    {
      "id": "rose",
      "name": "Rosé",
      "full_name": "Roseanne Park",
      "role": ["main_vocalist", "dancer"],
      "solo_songs": ["on_the_ground", "gone", "hard_to_love"],
      "strengths": ["emotional_vocals", "unique_tone", "acoustic"],
      "performance_style": "emotional and captivating",
      "vocal_range": "soprano"
    },
    {
      "id": "lisa",
      "name": "Lisa",
      "full_name": "Lalisa Manoban",
      "role": ["main_dancer", "rapper"],
      "solo_songs": ["lalisa", "money"],
      "strengths": ["dance", "stage_command", "rap"],
      "performance_style": "explosive and dynamic",
      "vocal_range": "mezzo-soprano"
    }
  ]
}
```

---

#### `.claude/skills/blackpink/resources/filter-songs.sh`

```bash
#!/bin/bash
# filter-songs.sh - Filter songs from songs.json by criteria
# Usage: filter-songs.sh <songs.json path> <filter_type> <filter_value>
#
# Filter types:
#   mood <tag>         - Songs matching a mood tag (e.g., "fierce")
#   energy <min> <max> - Songs within energy range (e.g., 70 100)
#   member <id>        - Solo songs for a specific member (e.g., "lisa")
#   suitable <role>    - Songs suitable for a setlist role (e.g., "opener")
#   bpm <min> <max>    - Songs within BPM range
#
# Output: JSON array of matching song objects

SONGS_FILE="$1"
FILTER_TYPE="$2"

if [ ! -f "$SONGS_FILE" ]; then
  echo '{"error": "songs.json not found at: '"$SONGS_FILE"'"}'
  exit 1
fi

case "$FILTER_TYPE" in
  mood)
    TAG="$3"
    jq --arg tag "$TAG" '[.songs[] | select(.mood | index($tag))]' "$SONGS_FILE"
    ;;
  energy)
    MIN="$3"
    MAX="$4"
    jq --argjson min "$MIN" --argjson max "$MAX" \
      '[.songs[] | select(.energy >= $min and .energy <= $max)]' "$SONGS_FILE"
    ;;
  member)
    MEMBER="$3"
    jq --arg m "$MEMBER" \
      '[.songs[] | select((.members_featured | length) == 1 and (.members_featured | index($m)))]' \
      "$SONGS_FILE"
    ;;
  suitable)
    ROLE="$3"
    jq --arg role "$ROLE" '[.songs[] | select(.suitable_for | index($role))]' "$SONGS_FILE"
    ;;
  bpm)
    MIN="$3"
    MAX="$4"
    jq --argjson min "$MIN" --argjson max "$MAX" \
      '[.songs[] | select(.bpm >= $min and .bpm <= $max)]' "$SONGS_FILE"
    ;;
  *)
    echo '{"error": "Unknown filter type: '"$FILTER_TYPE"'. Use: mood, energy, member, suitable, bpm"}'
    exit 1
    ;;
esac
```


## 5. マッピングルール（CC → GHC 変換）

> **最終確認日: 2026-05-25**
> CC: code.claude.com/docs/en/ (sub-agents, skills, agent-sdk/slash-commands)
> GHC: code.visualstudio.com/docs/copilot/ (agents/subagents, customization/custom-agents, customization/prompt-files, customization/agent-skills, agents/agent-tools)

### 5.1 ファイル配置

| レイヤー         | CC                                    | GHC                                    |
|-----------------|---------------------------------------|----------------------------------------|
| エントリポイント  | `.claude/commands/<name>.md`          | `.github/prompts/<name>.prompt.md`     |
| スキル           | `.claude/skills/<name>/SKILL.md`      | `.github/skills/<name>/SKILL.md`       |
| WF              | `.claude/skills/<name>/workflows/*.md`| `.github/skills/<name>/workflows/*.md` |
| リソース         | `.claude/skills/<name>/resources/*`   | `.github/skills/<name>/resources/*`    |
| サブエージェント  | `.claude/agents/<name>.md`            | `.github/agents/<name>.agent.md`       |

> **補足:** GHCは `.claude/agents/` および `.claude/skills/` ディレクトリも直接読み込み可能（Claudeフォーマット互換）。変換せずとも動作する可能性があるが、本設計ではGHCネイティブ形式への変換を行う。

### 5.2 フロントマター変換

**エントリポイント：**

| CC フィールド              | GHC フィールド                      | 備考                          |
|--------------------------|-------------------------------------|-------------------------------|
| `description: ...`       | `description: ...`                  | YAML標準の文字列記法で統一      |
| `command: /name`         | （不要）                             | GHCはファイル名がコマンド名     |
| `allowed-tools: ...`     | `tools: [...]`                      | 配列形式に変換＋ツール名マッピング適用 |
| （なし）                  | `agent: agent`                      | GHCフィールドを追加。`agent` の他に `ask`, `plan`, カスタムエージェント名も指定可 |
| `model: ...`             | `model: ...`                        | モデル名をGHC形式に変換（5.5参照） |

**スキル（SKILL.md）：**

| CC フィールド                   | GHC フィールド                   | 備考                          |
|-------------------------------|----------------------------------|-------------------------------|
| `name`                        | `name`                           | そのまま（CC/GHC共通）         |
| `description`                 | `description`                    | そのまま（CC/GHC共通）         |
| `disable-model-invocation`    | `disable-model-invocation`       | そのまま（CC/GHC共通）         |
| `user-invocable`              | `user-invocable`                 | そのまま（CC/GHC共通）         |
| `argument-hint`               | `argument-hint`                  | そのまま（CC/GHC共通）         |
| `allowed-tools`               | `tools: [...]`                   | フィールド名変更＋ツール名マッピング適用 |
| `model`                       | `model`                          | モデル名をGHC形式に変換（5.5参照） |
| `context: fork`               | `context: fork`                  | そのまま（CC/GHC共通、GHC側はExperimental） |

ただしプロンプト本文中のパス参照を変換する必要がある（`.claude/skills/` → `.github/skills/`）。
WF・リソースファイルはパス参照の変換のみ。

**サブエージェント：**

| CC フィールド              | GHC フィールド                      | 備考                          |
|--------------------------|-------------------------------------|-------------------------------|
| `name: my-agent`         | `name: my-agent`                    | そのまま（プレフィックス不要）   |
| `description: ...`       | `description: ...`                  | そのまま                       |
| `tools: Read, Grep`      | `tools: ['readFile', 'search/codebase']` | ツール名マッピング適用（5.4参照） |
| `model: sonnet`          | `model: Claude Sonnet 4.6 (copilot)` | モデル名をGHC形式に変換（5.5参照） |
| `disallowedTools: ...`   | （削除）                             | GHCに対応フィールドなし         |
| `permissionMode: ...`    | （削除）                             | GHCに対応フィールドなし         |
| `maxTurns: ...`          | （削除）                             | GHCに対応フィールドなし         |
| `memory: ...`            | （削除）                             | GHCに対応フィールドなし         |
| `skills: [...]`          | （削除）                             | GHCに対応フィールドなし         |
| `hooks: ...`             | `hooks: ...`                        | GHCもサポート（Preview）。形式は異なる可能性あり |
| （なし）                  | `target: vscode`                    | GHCフィールドを追加             |
| （なし）                  | `agents: [...]`                     | GHC固有。サブエージェント呼び出し制限（省略時は制限なし） |
| `user-invocable`         | `user-invocable`                    | CC/GHC共通                     |
| `disable-model-invocation` | `disable-model-invocation`        | CC/GHC共通                     |

> **旧版からの変更:**
> - `_` プレフィックス規則を削除。GHC公式ドキュメントに記載なし。Qiita記事の著者独自規約であった
> - `model` フィールドを「削除」から「変換」に変更。GHCが `model` フィールドをサポートするようになった
> - `user-invocable`, `disable-model-invocation` を追加。CC/GHC双方で同名フィールドが追加された

### 5.3 プロンプト本文の変換

| 項目                        | CC                               | GHC                               |
|-----------------------------|----------------------------------|------------------------------------|
| サブエージェント呼び出し      | `<name> サブエージェントに依頼`    | `<name> をサブエージェントとして起動` |
| リソースパス                 | `.claude/skills/blackpink/...`   | `.github/skills/blackpink/...`     |
| 引数テンプレート             | `$ARGUMENTS`                     | 変換方法は下記参照                   |

**引数テンプレートの変換:**

CC と GHC で引数の扱いが異なる。

| 用途                | CC                                | GHC prompt files              | GHC skills/agents       |
|--------------------|-----------------------------------|-------------------------------|-------------------------|
| 全引数展開          | `$ARGUMENTS`                      | テンプレート変数なし（末尾に自動付与） | テンプレート変数なし（コンテキストとして流入） |
| 位置引数            | `$ARGUMENTS[N]` / `$N`           | `${input:variableName}`       | 非対応                   |
| 名前付き引数        | `$name`（arguments宣言必要）       | `${input:variableName}`       | 非対応                   |

- **エントリポイント（commands → prompt files）:** `$ARGUMENTS` を含む行を削除し、ユーザー入力が末尾に自動付与される前提で本文を調整する。位置引数を使っている場合は `${input:name}` に書き換える
- **スキル・WF内:** GHCスキルはテンプレート変数非対応。`$ARGUMENTS` を含む行を自然言語の指示に書き換える（例: 「ユーザーが指定したテーマ」）

### 5.4 ツール名マッピング

| CC              | GHC (VS Code)          | 備考                    |
|-----------------|------------------------|------------------------|
| `Read`          | `readFile`             | ファイル読み込み          |
| `Grep`          | `search/codebase`      | テキスト検索             |
| `Glob`          | `search/codebase`      | ファイルパターン検索（Grepと同一ツールに集約） |
| `Write`         | `createFile`           | ファイル新規作成          |
| `Edit`          | `editFiles`            | ファイル編集             |
| `Bash`          | `runInTerminal`        | コマンド実行             |
| `Agent`         | `agent/runSubagent`    | サブエージェント呼び出し   |

> **注意:** GHCにはツールセット（`search`, `read`, `edit`, `execute`, `agent`）とその配下の個別ツール（`search/codebase`, `readFile` 等）がある。ツールセットを指定するとグループ内の全ツールが有効になる。上表は個別ツール名を記載しているが、`tools: ['search', 'readFile', 'runInTerminal', 'agent/runSubagent']` のようにツールセットと個別ツールを混在指定できる。
>
> GHCのツール名はUI経由で設定すると誤った形式で書き込まれる既知バグがある（[microsoft/vscode-copilot-release#14104](https://github.com/microsoft/vscode-copilot-release/issues/14104)）。手動記述を推奨。

### 5.5 モデル名マッピング

| CC              | GHC (VS Code)                      | 備考                    |
|-----------------|------------------------------------|------------------------|
| `haiku`         | `Claude Haiku 4.5 (copilot)`      | 軽量・高速              |
| `sonnet`        | `Claude Sonnet 4.6 (copilot)`     | バランス型              |
| `opus`          | `Claude Opus 4.6 (copilot)`       | 高性能                  |
| `inherit`       | （フィールド省略）                   | 親のモデルを継承         |

> GHCの `model` フィールドは配列も受け付ける（優先順にフォールバック）。単一モデルの場合は文字列で指定。


## 6. GHC側の前提設定

```json
{
  "chat.subagents.allowInvocationsFromSubagents": true
}
```

> **補足設定（必要に応じて）:**
> - `chat.agentFilesLocations`: カスタムエージェントファイルの追加検索パス
> - `chat.agentSkillsLocations`: スキルファイルの追加検索パス
> - `chat.useCustomizationsInParentRepositories`: 親リポジトリからのカスタマイズ読み込み（モノレポ向け）
> - `chat.useCustomAgentHooks`: エージェントスコープのフック有効化（Preview）

> **旧版からの変更:** `chat.customAgentInSubagent.enabled` → `chat.subagents.allowInvocationsFromSubagents` に設定名が変更された。


## 7. 変換スクリプトの方針

マッピングルール（セクション5）をYAML/JSONのルールファイルとして定義し、Python/bashスクリプトで機械変換する。

処理内容:
1. CC側のファイルを読み込む
2. フロントマターのフィールドをマッピングテーブルに従って変換する
3. プロンプト本文中のリソースパス・サブエージェント呼び出しパターンを置換する
4. ツール名を変換する（5.4参照）
5. モデル名を変換する（5.5参照）
6. 引数テンプレートを変換する（5.3参照）
7. GHC側のディレクトリ構造に配置する

仕様変更時はマッピングテーブルのみ更新すればよい。


## 8. 検証方法

同じテーマでCC/GHC両方で実行し、結果を比較する。

### 検証シナリオ

| # | コマンド                                 | 期待WF    | サブエージェント呼び出し順              |
|---|----------------------------------------|-----------|---------------------------------------|
| 1 | `/bp fierce and powerful`              | optimized | finder → evaluator → planner          |
| 2 | `/bp quick simple party setlist`       | quick     | finder → planner                      |
| 3 | `/bp fierce vs emotional compare`      | versus    | finder(A) + finder(B) → evaluator → planner |

### 確認ポイント

- WFの振り分けが正しいか
- サブエージェント間のJSON受け渡しが壊れていないか
- リソースファイル（songs.json, members.json）が正しく参照されているか
- filter-songs.sh が実行されているか
- エラーJSON形式が統一されているか
- CC版とGHC版で同等の結果が得られるか


## 9. 情報源

| 項目                          | URL / 出典                                                        |
|-------------------------------|------------------------------------------------------------------|
| CC サブエージェント公式          | https://code.claude.com/docs/en/sub-agents                        |
| CC スキル公式                   | https://code.claude.com/docs/en/skills                            |
| CC スラッシュコマンド            | https://code.claude.com/docs/en/agent-sdk/slash-commands          |
| GHC サブエージェント公式         | https://code.visualstudio.com/docs/copilot/agents/subagents       |
| GHC カスタムエージェント         | https://code.visualstudio.com/docs/copilot/customization/custom-agents |
| GHC プロンプトファイル           | https://code.visualstudio.com/docs/copilot/customization/prompt-files |
| GHC エージェントスキル           | https://code.visualstudio.com/docs/copilot/customization/agent-skills |
| GHC エージェントツール           | https://code.visualstudio.com/docs/copilot/agents/agent-tools     |
| GHC ツール名バグ                | https://github.com/microsoft/vscode-copilot-release/issues/14104  |
| GHC runSubagent実践 (Qiita)    | https://qiita.com/kenc_app/items/836bf0fa987884ff63da             |
| GHC サブエージェント検証 (Zenn)  | https://zenn.dev/openjny/articles/2619050ec7f167                  |
| CC サブエージェント出力仕様      | 「The parent receives the subagent's final message verbatim」(SDK docs) |


## 10. 制約・注意事項

- 全レイヤー間のデータ受け渡しはLLMのプロンプト解釈を経由する。確定的な関数呼び出しではない
- サブエージェントのJSON出力も「LLMがJSON形式で返す」という指示に基づくもの。100%保証ではない
- CC側のサブエージェントはネスト不可（built-in制約）。GHC側はデフォルト無効、`chat.subagents.allowInvocationsFromSubagents` で有効化可能（最大深度5）。本設計では深さ1のみ使用
- GHCのツール名はバージョンアップやUI操作で変わりうる。マッピングテーブルの定期更新が必要
- GHCは `.claude/` ディレクトリからもエージェント・スキルを読み込めるため、変換なしでの動作確認も検討に値する


## 11. stage-A 検証結果（CC/GHC 比較）

> **測定日**: 2026-06-17  
> **モデル**: CC・GHC ともに Sonnet 4.6（D-4 参照）  
> **対象**: 3WF（quick / optimized / versus）× 各2ラン、計6ラン/プラットフォーム（有効ラン。修正前の FAIL ランは参考扱い）

### 11.1 layer-2 不変条件

| 条件 | 内容 |
|------|------|
| C1 | SKILL.md が WF を選択して実行する |
| C2 | filter-songs.sh の実行インデックスが transcript に記録される |
| C3 | filter-songs.sh が実際に実行される（bash 呼び出し確認） |
| C4 | S1/S2/S3 の OUT 形式がすべて揃っている |
| C5 | サブエージェントへの委譲が発生していない（stage-A は委譲なし） |

### 11.2 測定結果サマリー

**CC（有効6ラン・修正後）**

| WF | ラン数 | C1 | C2 | C3 | C4 | C5 | 総合 |
|---|---|---|---|---|---|---|---|
| quick | 2 | PASS | PASS | PASS | PASS | PASS | 2/2 PASS |
| optimized | 2 | PASS | PASS | PASS | PASS | PASS | 2/2 PASS |
| versus | 2 | PASS | PASS | PASS | PASS | PASS | 2/2 PASS |

**GHC（修正後再測定）**

| WF | ラン数 | C1 | C2 | C3 | C4 | C5 | 総合 |
|---|---|---|---|---|---|---|---|
| quick | 2 | PASS | PASS | PASS | obs.limit→画面 PASS ※ | PASS | 2/2 PASS ※ |
| optimized | 2 | PASS | PASS | PASS | obs.limit→画面 PASS ※ | PASS | 2/2 PASS ※ |
| versus | 2 | PASS | PASS | PASS | obs.limit→画面 PASS ※ | PASS | 2/2 PASS ※ |

※ C4 のみ手動画面確認による PASS。他の条件（C1–C3・C5）は verify-run.py で自動判定。

> **修正前の初回測定（参考）:**  
> CC（Sonnet 4.6）: quick 2/2 PASS, versus 2/2 PASS, optimized 0/2 PASS（C3 FAIL: db178cd7・c207ab91）。WF 別に修正・再測定を実施したため「初回6ラン一括」のベースラインは存在しない。  
> GHC（Sonnet 4.6）: quick 2/2 PASS, optimized 2/2 PASS, versus 1/2 PASS（88dfcb11 C2・C3 FAIL）。

### 11.3 差分分類

#### 実装工夫で解決済み

| 問題 | 発生プラットフォーム | 根本原因 | 修正内容 | 修正コミット |
|---|---|---|---|---|
| C3 FAIL（filter-songs.sh 未実行） | CC・GHC 共通 | WF に利用可能 mood タグリストがなく、LLM が songs.json を直参照した | 全 WF ファイルに 38 種タグリスト + `Use only filter-songs.sh` 肯定的制約を追加 | 0134464・0de0805 |
| C2 FAIL（filter-run インデックスなし） | GHC（88dfcb11） | C3 FAIL の下流効果。filter-songs.sh が実行されなければ transcript に実行インデックスも残らない | C3 FAIL の修正（上記）と同一。C3 修正後 C2 は自動的に回復 | 0134464 |

補足：GHC では同テーマ同 WF で 88dfcb11 が FAIL し 12d09754 が PASS した（修正前）。これはタグリスト欠如という共通原因への LLM の非決定的な応答として現れたものであり、プラットフォーム固有の非決定性ではなく WF の曖昧さが誘因と判断する。修正後は両プラットフォームとも FAIL なし。

#### プラットフォーム固有制約として残る

| 制約 | プラットフォーム | 内容 | 影響範囲 | 対処方法 |
|---|---|---|---|---|
| transcript 最終 OUT 欠落（obs.limit） | GHC のみ | GHC transcript が最終 OUT 生成ターンを記録しない。verify-run.py で C4 の自動判定が不能となる | C4 (OUT形確認) の自動判定 → 全 GHC ランで obs.limit | 画面出力の手動確認で補完（verify-run.py の GHC 用注釈として記録） |

CC は transcript に最終 OUT が含まれるため C4 が自動判定できる。GHC は obs.limit が構造的であり、プロンプト変更では解消できないプラットフォーム制約。

### 11.4 検証プロセスの差分

| 項目 | CC | GHC |
|---|---|---|
| C1–C3・C5 自動判定 | verify-run.py で可 | verify-run.py で可 |
| C4 自動判定 | verify-run.py で可 | 不能（obs.limit） |
| C4 判定方法 | 自動 | 画面出力を手動確認で補完 |
| コールドセッション要件 | 必須（測定はユーザー操作） | 必須（測定はユーザー操作） |
