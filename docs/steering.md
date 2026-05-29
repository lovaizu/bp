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
- [x] **フェーズ2-0完了**（2026-05-25） — マッピングテーブルを公式ドキュメントに基づき最新化（設計書セクション5）
- [x] **フェーズ2-1完了**（2026-05-25） — 変換スクリプト作成（`scripts/convert-cc-to-ghc.py` + `scripts/mapping-rules.json`）
- [x] **フェーズ2-2完了**（2026-05-25） — GHC側ファイル生成済み
- [x] **フェーズ2-3完了**（2026-05-29） — GHC側テスト 3シナリオ全PASS（optimized×2・quick・versus）
- **フェーズ2-4 実施中**（2026-05-29〜） — CC/GHC結果比較。比較は「比較 → 根本原因分析 → 改善案合意 → 改善 → 再測定」の改善ループの入口。まず 2-4a（差分の特定）から


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

### GHC側テスト（2026-05-27〜29、フェーズ2-3）

**テスト前の修正:**
- prompt file に `tools: ['agent', 'read', 'search', 'execute']` が必要と判明。マッピングルール・変換スクリプトに反映済み
- `/bp` 呼び出し（prompt file 経由）は tools 追加後に動作確認済み
- `/blackpink` 呼び出し（スキル直接）は tools 追加前から動作

**GHCテスト1回目（`/blackpink white`、スキル直接呼び出し）:**

| 確認ポイント | 結果 | 備考 |
|-------------|------|------|
| WF選択 | **OK** | optimized を選択 |
| サブエージェント呼び出し | **OK** | finder → evaluator → planner の3段 |
| filter-songs.sh 実行 | **OK** | 12回実行 |
| 曲数 | **OK** | 9曲、全曲DB内 |
| evaluator モデル指定 | **要注意** | 1回目失敗→モデル指定なしでリトライ成功 |
| JSON受け渡し | **要注意** | evaluator→planner 間で1回失敗→リトライ成功 |

**GHCテスト2回目（`/bp white`、prompt file 経由）:**

| 確認ポイント | 結果 | 備考 |
|-------------|------|------|
| prompt → skill | **OK** | `bp.prompt.md` → `SKILL.md` 読み込み成功 |
| WF選択 | **OK** | optimized を選択 |
| サブエージェント呼び出し | **OK** | finder → evaluator → planner の3段 |
| filter-songs.sh 実行 | **OK** | 7回以上実行 |
| 曲数 | **OK** | 8曲、全曲DB内 |
| evaluator モデル指定 | **OK** | リトライなし |
| JSON受け渡し | **要注意** | evaluator/plannerがcodebase検索を繰り返す |

**GHCテスト3回目（`/bp` party、prompt file 経由、quick WF）:**

| 確認ポイント | 結果 | 備考 |
|-------------|------|------|
| WF選択 | **OK** | "quick party setlist" → quick を選択 |
| サブエージェント呼び出し | **OK** | finder → planner の2段（evaluator スキップ＝quick仕様通り）|
| filter-songs.sh 実行 | **OK** | mood party + energy 80 100 の2回（ログ12:38に一致）|
| 曲数 | **OK** | 8曲（上限8ちょうど）、全曲DB内 |
| JSON受け渡し | **要注意** | planner呼び出し前にcodebase検索を複数回（既知課題1の再現）|
| planner最終出力 | **要注意** | JSON構造でなく整形済みmarkdown（既知課題3の再現）|

**GHCテスト4回目（`/bp fierce vs emotional`、prompt file 経由、versus WF）:**

| 確認ポイント | 結果 | 備考 |
|-------------|------|------|
| WF選択 | **OK** | "fierce vs emotional" → versus を選択 |
| サブエージェント呼び出し | **OK** | finder(A)+finder(B) → evaluator(comparative) → planner の4段（versus仕様通り）|
| filter-songs.sh 実行 | **OK** | fierce側3回 + emotional側3回 = 計6回（ログ13:11-13:12に一致）|
| 曲数 | **OK** | fierce 8曲・emotional 8曲（目標8-10内）、全曲DB内 |
| comparative推薦 | **OK** | fierce を推薦、flow score 88 vs 82 提示 |
| JSON受け渡し | **要注意（悪化）** | 課題1が最も顕著。codebase検索多数 + session store照会 + copilot-chatの session-resources/content.json を直接Readして中間結果を復元 |

**フェーズ2-3 GHCテスト完了:** optimized×2 / quick / versus の3シナリオ全PASS（マクロ構造）。

**確認済み課題（当初メモ。2-4a続報で訂正済み — 下記「2-4a 続報」参照）:**
1. evaluator/planner がcodebase検索を繰り返す（JSON受け渡しが不安定）に見えた → **訂正: GHCの正規リソース受け渡し。planner出力は無傷。中間ホップのみ未検証**
2. evaluator のモデル名指定が失敗することがある → **本物（有効）**
3. planner の最終出力がmarkdown → **訂正: 誤り。plannerは有効JSONを返す。markdownは親のStep4整形＝設計通り**

### 2-4a 差分の特定（2026-05-29）

**決定的事実:** GHC側エージェント定義にもJSON契約は保たれている（planner/evaluator とも `Return ONLY the following JSON. No other text.` が変換後も残存、model も変換済み）。→ 差分はマッピング欠落ではなく**ランタイム挙動**で発生している。

| # | 差分 | CC（事実） | GHC（事実） | GHC固有か |
|---|------|-----------|-----------|----------|
| 1 | JSON受け渡し | 全シナリオでJSON形式を維持（steering記録） | codebase検索・session store照会・chat-resource直接Readで中間結果を復元 | **YES**（定義契約は同一なのにランタイムで逸脱） |
| 2 | モデル名指定 | `sonnet` で動作 | `Claude Sonnet 4.6 (copilot)` がevaluatorで時々失敗→指定なしリトライで成功 | **YES**（間欠的） |
| 3 | 最終出力形式 | 個別追跡なし（CC側 planner サブエージェント出力形式は未記録） | planner が markdown を直接返却 | **要確認**（CC側の事実不足） |

### 2-4a 続報: content.json 解析による差分1・3の訂正（2026-05-29）

GHCのチャット session-resources を解析（`chat-session-resources/*/call_*/content.json` + `schema.json`、本日3セッション分）。

**事実:**
- 3セッションすべてに `schema.json` が自動生成され（3/3）、`content.json` は**全て有効JSON・planner契約に完全準拠**（single×1=white, comparative×2）。
- これらは GHC が**大きな構造化サブエージェント出力を「リソース」として実体化する正規の仕組み**。サブエージェントのJSON返り値を保存しJSON Schemaを自動生成、親がそれを読む（＝CCのプロンプト内JSON受け渡しのGHC版）。

**当初課題の訂正:**

| 当初課題 | 訂正後の判定 | 根拠 |
|---------|------------|------|
| 3. planner が markdown を返す | **誤り（反証）** | planner出力は全ケース有効JSON。ユーザーが見た markdown は親のStep 4「Present to user」整形＝設計通り。トレースの最終表示を返り値と誤読していた |
| 1. 受け渡しが壊れ内部ファイルから即興復元 | **大幅縮小・再定義** | content.json読込はGHCの正規リソース受け渡し。planner→親のJSONは3件とも無傷。残る論点は finder→evaluator / evaluator→planner の中間ホップ（リソース化されず未捕捉）のみ。ただしplanner出力が正しいsong_id/データを含む＝正しいデータは最終的に流れている |
| 2. モデル名指定の失敗 | **有効（本物）** | 実際にリトライ発生。content.jsonとは無関係に成立 |

**結論:** 「3つの課題」のうち課題3は消滅、課題1は中間ホップのみに縮小、課題2だけが本物。当初トレースの語り口を誤読していた。
**2-4a 残論点:** 中間ホップ（finder→evaluator、evaluator→planner）が clean かは未捕捉。必要なら 2-4b で扱う。


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

- [x] **2-0. マッピングテーブル最新化** — 2026-05-25 完了
  - CC公式 3ページ + GHC公式 5ページ + Qiita/Zenn 2記事 + GH Issue 1件を確認
  - 主な更新: `_`プレフィックス規則削除、model変換追加、ツール名全面更新、設定名変更、引数テンプレート差異の明記
  - 設計書セクション5に「最終確認日: 2026-05-25」を記載済み
- [x] **2-1. 変換スクリプト作成** — 2026-05-25 完了
  - `scripts/mapping-rules.json`: ツール名・モデル名・パス・フロントマター変換ルール
  - `scripts/convert-cc-to-ghc.py`: ルールファイルを読み込みCC→GHC変換を実行（`--dry-run` 対応）
- [x] **2-2. GHC側ファイル生成** — 2026-05-25 完了
  - 11ファイル生成（prompts×1, agents×3, skills×1, workflows×3, resources×3）
  - 全`.claude/`パス参照が`.github/`に変換済み、残存なし確認済み
- [x] **2-3. GHC側テスト** — 2026-05-29 完了。optimized（white）2回 + quick（party）+ versus（fierce vs emotional）全PASS
- [ ] **2-4. CC/GHC結果比較** — 「同等の結果」が得られているかを判定し、同等でない点を改善ループに渡す
  - [x] **2-4a. 差分の特定** — 2026-05-29 完了。3差分を列挙後、content.json解析で訂正（テスト結果サマリ「2-4a」「2-4a続報」参照）。結論: 課題3消滅、課題1は中間ホップのみに縮小、課題2が本物
  - [ ] **2-4b. 根本原因分析と分類** — 各差分を事実ベースで分析し、(A)マッピング/定義側で修正可能 / (B)プラットフォーム固有の限界 に分類
  - [ ] **2-4c. 改善ループ** — (A)について改善案を出す → 合意 → 改善 → GHCで再測定。同等になるまで繰り返す。(B)は制約として記録

### フェーズ3: 仕上げ（改善ループ収束後）

- [ ] **3-1. マッピングテーブル確定** — 改善ループの結果を反映してルールを最終化
- [ ] **3-2. 設計書更新** — 検証で得た知見・制約（(B)の固有限界含む）を設計書に反映


## 参照ファイル

| ファイル | 内容 |
|---------|------|
| `docs/cross-platform-agent-design.md` | 設計書本体 |
| `.claude/commands/bp.md` | エントリポイント |
| `.claude/skills/blackpink/SKILL.md` | スキル（WF振り分け） |
| `.claude/skills/blackpink/workflows/*.md` | ワークフロー定義 |
| `.claude/agents/bp-*.md` | サブエージェント定義 |
| `.claude/skills/blackpink/resources/` | 楽曲DB・メンバー情報・フィルタスクリプト |
