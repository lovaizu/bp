# Steering: CC/GHC クロスプラットフォーム エージェント検証

## ゴール

Claude Code（CC）で設計した4層エージェントアーキテクチャが設計通りに動くことを確認し、
マッピングルールでGitHub Copilot（GHC）に機械変換して同等の結果を得る。

BLACKPINKセットリスト・プランナーは検証用サンプル。プランナー自体が目的ではない。


## ルール

- 推測せず事実ベースで判断する
- steering.md を実態に合わせて都度更新する
- コミットは目的単位に行う
- 変更したらコミット、プッシュする


## 現在地

- [x] 設計書完成 (`docs/cross-platform-agent-design.md`)
- [x] CC側ファイル一式を実装・配置済み
- [x] 初回テスト実行（`/bp` → High Energy Party テーマ）


## 初回テストで判明した問題

2026-05-21 に `/bp` を実行。結果は出たが、設計通りではなかった。

| 問題 | 詳細 |
|------|------|
| WF未参照 | 親エージェントが SKILL.md / optimized.md を読まず、自己判断でサブエージェントを呼んだ |
| 曲数超過 | 設計: finder最大12曲 → evaluator が8-10曲に絞る。実際: 18曲のまま最後まで流れた |
| 役割逸脱 | evaluator は「順番を決める」役だが、親が順番を決めて evaluator に確認させた |
| JSON受け渡し崩壊 | 設計: サブエージェント結果JSONをそのまま次に渡す。実際: 自然言語で再解釈して渡した |

根本原因: 親エージェント（Claude Code本体）がワークフローMDの指示に従わず自由に振る舞った。


## タスク

### フェーズ1: CC側を設計通りに動かす

- [ ] **1-1. 原因分析** — 親がWFを読まなかった原因を特定する
  - SKILL.md の指示が弱い？ skill呼び出し時にSKILL.mdが読まれていない？
  - WFの「Delegate to...」指示が親エージェントのAgentツール呼び出しに繋がっていない？
- [ ] **1-2. SKILL.md / WF 改修** — 原因に基づいてプロンプトを修正
- [ ] **1-3. サブエージェント改修** — 必要に応じてagent定義を修正
- [ ] **1-4. 検証シナリオ実行** — 設計書セクション8の3シナリオを実行
  - [ ] `/bp fierce and powerful` → optimized WF
  - [ ] `/bp quick simple party setlist` → quick WF
  - [ ] `/bp fierce vs emotional compare` → versus WF
- [ ] **1-5. 確認ポイント検証** — 各シナリオで以下を確認
  - WF振り分けが正しいか
  - サブエージェント間のJSON受け渡しが壊れていないか
  - リソースファイルが正しく参照されているか
  - filter-songs.sh が実行されているか
  - エラーJSON形式が統一されているか

### フェーズ2: GHC変換

- [ ] **2-1. 変換スクリプト作成** — 設計書セクション5のマッピングルールに基づく
- [ ] **2-2. GHC側ファイル生成** — スクリプトで `.github/` 配下に変換出力
- [ ] **2-3. GHC側テスト** — 同じ3シナリオをGHC（VS Code Copilot）で実行
- [ ] **2-4. CC/GHC結果比較** — 同等の結果が得られるか確認

### フェーズ3: 仕上げ

- [ ] **3-1. マッピングテーブル確定** — 検証結果を反映してルールを最終化
- [ ] **3-2. 設計書更新** — 検証で得た知見・制約を設計書に反映


## 参照ファイル

| ファイル | 内容 |
|---------|------|
| `docs/cross-platform-agent-design.md` | 設計書本体 |
| `.claude/commands/bp.md` | エントリポイント |
| `.claude/skills/blackpink/SKILL.md` | スキル（WF振り分け） |
| `.claude/skills/blackpink/workflows/*.md` | ワークフロー定義 |
| `.claude/agents/bp-*.md` | サブエージェント定義 |
| `.claude/skills/blackpink/resources/` | 楽曲DB・メンバー情報・フィルタスクリプト |
