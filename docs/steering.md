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
- [x] 4回目テスト実行（`/bp white` テーマ、2026-05-21）
- [x] 5回目テスト実行（`/bp white` テーマ、2026-05-24） — filter-songs.sh ログ検証で全項目クリア
- [x] 検証シナリオ3本実行（2026-05-24） — optimized / quick / versus 全パス
- [x] **フェーズ1完了**（2026-05-24）
- **次のステップ: フェーズ2-0** — CC/GHC公式ドキュメントを確認し、マッピングテーブル（設計書セクション5）が最新か検証する


## テスト結果サマリ

### 初回〜3回目（High Energy Party）

全3回とも **マクロ構造は設計通り**: Skill起動 → bp-song-finder → bp-setlist-evaluator → bp-show-planner の順次呼び出しが正しく行われた。

**全3回とも守られなかったミクロの制約:**

| 問題 | 設計 | 実際（3回共通） |
|------|------|----------------|
| WF未参照 | optimized.md を Read で読み、そこに書かれた制約に従う | 親が WF を読まず、暗黙知でフローを再現。結果的に正しい順序だが制約は無視 |
| 曲数超過 | finder 最大12曲 → evaluator が8-10曲に絞る | finder 15-21曲 → evaluator が絞らない or 増やす |
| 役割逸脱 | evaluator が自律的に曲選別・並び替え・スコアリング | 親が先に判断し、evaluator に確認・追認させる |
| JSON受け渡し | finder の JSON をそのまま evaluator に渡す | 親が JSON を自然言語に再解釈して渡す |

**根本原因（2段階）:**

1. **WFを読まない:** SKILL.md はコンテキスト注入であり実行境界ではない。`.claude/agents/` 内の定義が親コンテキストに自動ロードされるため、親は WF を読まなくてもサブエージェントの存在と役割を知っており、正しい順序で呼べてしまう。
2. **読んでも従わない可能性:** WF には明確な作業指示がある（テンプレ形式のリクエスト、「Max songs: 12」、「Do not modify the subagent's result」等）。指示が曖昧なのではなく、親が WF の指示よりも自分の判断を優先してプロンプトを自由に構成する。WF は実行計画であって実行制約ではないため、読んだとしても親の行動を強制できない。

**結論:** マクロ構造は動く。ミクロの制約は (1) WF を読まないから伝わらない、(2) 読んでも親の自律判断で上書きされうる、の2段階の問題がある。

### 4回目（white テーマ、2026-05-21）

**マクロ構造:** 設計通り。Skill起動 → optimized.md を Read → bp-song-finder → bp-setlist-evaluator → bp-show-planner の順次呼び出し。

**ミクロの制約:**

| 制約 | 結果 | 備考 |
|------|------|------|
| WF参照 | **OK** | optimized.md を Read で読み、Step 1-4 に従った |
| 曲数上限 | **OK** | finder が 10曲返却（上限12以内） |
| 役割分離 | **OK** | evaluator が独自に並び替え・スコアリングを実施 |
| JSON受け渡し | **概ねOK** | JSONをコードブロックとしてプロンプトに埋め込んだ（自然言語への再解釈はなし）|
| filter-songs.sh 実行 | **不明** | ログ仕込み前のため確認不可 |

**前回からの改善:** 4つの問題のうち3つ解消、1つ部分改善。

### 5回目〜検証シナリオ（2026-05-24）

filter-songs.sh にログ出力（`/tmp/bp-filter.log`）を仕込んだ上で、全4回の実行を検証。

**5回目（white テーマ、再実行）:**
全項目クリア。filter-songs.sh は8回実行された（mood×7 + energy×1）。

**検証シナリオ3本:**

| シナリオ | WF | 振り分け | 曲数 | 役割分離 | JSON | shell | 結果 |
|---------|-----|---------|------|---------|------|-------|------|
| `/bp fierce and powerful` | optimized | OK | OK (12→10) | OK (score 87) | OK | OK (5回) | **PASS** |
| `/bp quick simple party setlist` | quick | OK | OK (8) | OK (eval skip) | OK | OK (4回) | **PASS** |
| `/bp fierce vs emotional compare` | versus | OK | OK (9+8) | OK (comparative) | OK | OK (3+4回) | **PASS** |

**確認ポイント（タスク1-5）全項目の結果:**

| 確認ポイント | 結果 | 備考 |
|-------------|------|------|
| WF振り分けが正しいか | **OK** | 3本のWFすべて正しく選択された |
| サブエージェント間のJSON受け渡し | **OK** | 全シナリオでJSON形式を維持 |
| リソースファイルの参照 | **OK** | songs.json, members.json ともに正しいパスで参照 |
| filter-songs.sh の実行 | **OK** | 全シナリオでログ出力を確認（合計24回実行） |
| エラーJSON形式の統一 | N/A | 全シナリオ成功のためエラーパス未検証 |


## タスク

### フェーズ1: CC側を設計通りに動かす

- [x] **1-1. 原因分析** — 親がWFを読まなかった原因を特定する（2026-05-21 完了）
  - 根本原因: スキルはコンテキスト注入であり実行境界ではない。親エージェントはSKILL.md受け取り後に「Read and follow」を推奨として解釈し、既知のサブエージェント定義から自己判断でタスクを遂行する。
  - `.claude/agents/` 内のエージェント定義は親のコンテキストに自動ロードされるため、ワークフローファイルを読まなくても何をすべきか分かってしまう。
  - 詳細な分析は claude-code-agent の出力（2026-05-21）参照。
- [x] **1-2. 対策検討** — 不要と判断。4回目以降はプロンプト変更なしで全制約が守られた
- [x] **1-3. サブエージェント改修** — 不要と判断。現行定義で設計通りに動作
- [x] **1-4. 検証シナリオ実行** — 2026-05-24 完了
  - [x] `/bp fierce and powerful` → optimized WF
  - [x] `/bp quick simple party setlist` → quick WF
  - [x] `/bp fierce vs emotional compare` → versus WF
- [x] **1-5. 確認ポイント検証** — 2026-05-24 完了（エラーパス未検証を除き全項目クリア）

### フェーズ2: GHC変換

- [ ] **2-0. マッピングテーブル最新化** — CC/GHC両方の公式ドキュメントを確認し、設計書セクション5のマッピングが現在の仕様と一致するか検証する
  - CC公式: https://code.claude.com/docs/en/sub-agents, skills, slash-commands
  - GHC公式: https://code.visualstudio.com/docs/copilot/agents/subagents, custom-agents
  - 差分があればマッピングテーブルを更新
  - マッピングファイルに「最終確認日」を記載する（いつのドキュメントに基づくか追跡可能にする）
- [ ] **2-1. 変換スクリプト作成** — 最新化したマッピングルールに基づく
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
