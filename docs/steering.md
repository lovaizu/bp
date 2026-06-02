# Steering: CC/GHC クロスプラットフォーム エージェント検証

## ゴール

**CCとGHCの両方で多層サブエージェント構成を動かす「方法」を見つけ、再利用可能な形に確立する。**

具体的には、Claude Code（CC）で設計した4層エージェント構成を、マッピングルールで
GitHub Copilot（GHC）に機械変換し、両プラットフォームでサブエージェントが動くことを実証する。
成果物は ①動作する両プラットフォーム実装 ②マッピングルール／変換スクリプト ③方法と
プラットフォーム差（サブエージェントを動かす勘所）の文書化。

BLACKPINKセットリスト・プランナーは検証用サンプル。プランナー自体も「同等の出力」も目的ではなく、
「方法が両プラットフォームで動くか」を確かめる手段。


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
- **フェーズ2-4 実施中**（2026-05-29〜） — transcript解析で実行メカニズムを事実検証。①起動②別コンテキスト③スクリプト実行は実証。**④JSON受け渡しは非決定的（設計通りでない）と判明** = 両プラットフォーム共通の核心課題。「100%再現」等の過剰表現は撤回済み
- **アプローチ転換（2026-05-30）** — ビッグバン検証を中止し、**段階的ビルドアップ**に移行。要素が入り組んで1変数に絞れない／直すと別が壊れる、を解消するため。目的は不変。詳細は「## 新アプローチ」
- **A-1（CC）完了（2026-06-02）** — 3WF（optimized/quick/versus）を基準1のA版に再構築し全て層2 PASS。optimized `white`×2・quick `party`×2・versus `fierce vs emotional`×2 を `scripts/verify-run.py` で判定＝**6/6 ラン全PASS**。詳細は「## 新アプローチ → A-1 測定結果」。次は A-2（GHC自動変換）


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
| 1. 受け渡しが壊れ内部ファイルから即興復元 | ⚠️**この続報の判定は誤り。下記「2-4 transcript解析」で再訂正** | （当時の誤った記述）content.json読込はGHCの正規リソース受け渡し、正しいデータは最終的に流れている → **実際は transcript で「上流JSONが下流プロンプトに埋まらない回が多数」と判明。content.jsonは planner の出力に過ぎず入力受け渡しの証拠ではなかった** |
| 2. モデル名指定の失敗 | **有効（本物）** | 実際にリトライ発生。content.jsonとは無関係に成立 |

**結論（当時）:** 「3つの課題」のうち課題3は消滅、課題1は中間ホップのみに縮小、課題2だけが本物。
**⚠️ 続報の誤りについて:** 課題1を「正規機構・データは流れている」と過小評価したのは誤り。content.json（=plannerの出力）だけを見て入力側の受け渡しを推論したため。transcript解析（後述）で、上流JSONが下流に渡らない回が多数あると判明し再訂正した。教訓: 出力の妥当性から受け渡しの成否を推論しない。

### 2-4b 根本原因分析: 課題2（モデル指定の間欠失敗）（2026-05-29）

**公式仕様（VS Code custom agents, code.visualstudio.com/docs/copilot/customization/custom-agents）:**
- `model: Model Name (copilot)` 形式は正しい（公式例 `Claude Sonnet 4.5 (copilot)`）。→ `Claude Sonnet 4.6 (copilot)` はフォーマット妥当。
- `model` は配列指定でフォールバック可能（`['A','B']` → 利用可能なモデルが見つかるまで順に試す）。
- 省略時はモデルピッカー選択中のモデルにフォールバック。

**根本原因（事実ベース）:**
- フォーマットは原因でない（evaluator/planner は同一文字列 `Claude Sonnet 4.6 (copilot)` だが planner は失敗していない）。
- test1の「指定なしリトライで成功」＝ピッカーモデルへのフォールバック成功。公式の「指定モデル利用不可時」挙動と整合。
- → 正体は**指定モデル（Sonnet 4.6）の一時的な利用不可**。間欠的・plannerは成功・4.6は概ね利用可、という事実すべてと一致。

**分類:** 原因は (B) プラットフォーム固有（モデル一時可用性）。ただし (A) マッピング側で緩和可能 — 公式の配列フォールバックを使う。

**改善案（2-4c 1周目、要合意）:** mapping-rules.json のモデル値を単一文字列→フォールバック配列に変更し、変換スクリプトが `model:` を配列出力するよう対応 → GHC再生成 → GHC再測定。

### 2-4 実行メカニズムの検証: transcript解析（2026-05-29〜30）★最重要

目的「サブエージェントが両プラットフォームで動くか」の核心4問を、GHCの生実行ログ（`GitHub.copilot-chat/transcripts/*.jsonl`、6セッション）で事実検証。

**検証法:** 各 `runSubagent` ツールの execution_start〜complete の窓の内側に現れるツール呼び出し・`user.message`・turn を解析。窓内に専用プロンプト注入＋自前ツール実行があれば「別コンテキストで動いた」証拠。

| 核心の問い | 判定 | 証拠 |
|-----------|------|------|
| ①起動したか | **YES（直接ログ）** | 全6セッションで runSubagent の tool.execution_start が役割ごとに記録。推論でなくGHCの実行ログそのもの |
| ②別コンテキストか | **YES** | runSubagent窓の内側に subagent専用 user.message＋自前turn＋自前ツール。さらに「下流が上流データを見えていない」こと自体が文脈分離の裏付け |
| ③サブエージェントからスクリプト実行 | **YES（独立2ソース）** | finder窓の内側で run_in_terminal（bash filter-songs.sh）＋ `/tmp/bp-filter.log` に独立記録（タイムスタンプ一致） |
| ④JSON受け渡し | ⚠️**不安定（当初「条件付きYES」は誤り）** | 下流プロンプトへの上流JSON埋め込みが**回によって有/無**（下表）。埋め込まれない回は下流が session_store のテーブルを手探り（"Inspect session db"）＋ファイル再読込で**入力を再生成**。ストアへの書き込みも無い |

**④の訂正の根拠 — 下流プロンプトに上流JSONが埋まっているか（全セッション、promptLen）:**

| セッション | evaluator | planner |
|-----------|-----------|---------|
| 06320cad | ✓(4526) | — |
| 70d39bc2 | ✗(987)→✓(5095) | ✓✓ |
| abc8996c | ✓(5867) | ✓(3255) |
| 8408f970 | — | ✓(3941) |
| 1d3c4b27 | ✓(5995) | ✗(303)✗(303) |
| 00dcd57b | ✗(309) | ✗(317) |

→ 受け渡しは「想定通り」ではなく**非決定的**。親がWFの「上流JSONを下流に渡す」指示を守る回と、terseなプロンプトを自作してJSONを渡さない回がある。**CCフェーズ1の「親がWF指示を無視」と同じ失敗モードが両プラットフォーム共通で残存**。

**起動信頼性の参考値（runSubagent成否、全6セッション集計。少数サンプル・シナリオ偏りあり＝確定値でなく傾向）:**
```
finder    : 8 OK / 0 FAIL   (失敗未観測。ただしn=8、100%の証明ではない)
evaluator : 4 OK / 3 FAIL   (不安定の兆候。失敗率は未確定)
planner   : 6 OK / 1 FAIL
```
（注: optimized約3 / quick 1 / versus 2 と偏在。quickはn=1で再現性を語れない。「100%再現」は過剰表現だったため撤回）

**結論（訂正版）:** ①起動・②別コンテキスト・③スクリプト実行は**観測した全6セッションで構造が一致**し、機能として実証された（ただし統計的信頼性は少数サンプル）。一方 **④JSON受け渡しは設計通りでなく非決定的**で、これが両プラットフォーム共通の核心課題。課題2（モデル可用性）による起動失敗も別途残る。

### 2-4c 改善方針: ④JSON受け渡し — ベストプラクティス調査と決定（2026-05-30）

**公式調査（CC: code.claude.com/docs/en/sub-agents、GHC: code.visualstudio.com/docs/copilot/agents/subagents）:**
- 両プラットフォームとも **サブエージェントは隔離コンテキスト**。親が送ったプロンプトの内容しか見えない（「subagentはchatを知らず、chatもsubagentを知らない」）。
- データは **subagent→（要約を返す）→親→（関連情報を渡す）→次subagent** の **親仲介**が唯一の正攻法。サブエージェント間の直接チャネルは存在しない。
- 公式は「サブエージェントは**要約**を返す」設計。フルJSONの無損失受け渡しは粒に逆らう。

**決定:**
- ❌ 不採用: 「書き込み権限を付けてファイル側チャネルで受け渡す」。隔離モデルを壊す抜け道で、真因（親が渡さない）も直さない。
- ✅ 採用: **親仲介を確実にする**方向。(1) WFの受け渡し指示を強制力UP（隔離の理由を明記し「フルJSONを逐語で貼れ」と命令）、(2) ペイロード最小化（finderは song_id 等の最小情報を返す＝公式の「要約を返す」と一致）。
- 単一ソース（CC側）を編集→変換スクリプトでGHC再生成、の原則は維持。

**実装順:** まず (1) WF文言ハードニングを全3WFに適用（低リスク・観測された失敗に直撃）→ 再測定。不足なら (2) ペイロード最小化を追加。

### 2-4c 第1周 再測定結果（versus×3、改善後、2026-05-30）

(1) WF文言ハードニング（隔離理由の明記＋「フルJSONを逐語で貼れ」）を全3WFに適用しGHC再生成 → versus を3回再測定。

| run | 委譲数 | evaluator入力 | planner入力 | ④下流JSON埋込 |
|-----|-------|--------------|-------------|--------------|
| bd11d6e0 | **0** | 未委譲 | 未委譲 | 測定不可（親が全部インライン・filter-songs未実行） |
| d6245478 | 5 | ✓4152字 | ✓4194字 | **✓✓** |
| d68ff031 | 3 | ✓4109字 | 親インライン | **✓** |

**結論①（④は改善）:** 下流サブエージェント呼び出しでのフルJSON埋込は **3/3 成功**（改善前の comparable versus は 0/2 で terse）。親プロンプトに "verbatim" 文言も出現。**WFハードニングは目的達成。**

**結論②（新たに明確化した真のボトルネック）:** 委譲のされ方が非決定的（3回で 0段/全3段/2段）。**どのステップを委譲するかが安定しない**＝CCフェーズ1と同根の「親がWFを実行制約でなく助言として扱う」問題。④より上位の再現性ブロッカー。

**次手:** → 禁止ルール追加は**不採用**（フェーズ1で「WFは助言であり実行制約でない＝指示を足しても親は無視できる」と証明済み。禁止文の上塗りは同じ効かないレバー）。アプローチごと転換（下記）。


## 新アプローチ: 段階的ビルドアップ（2026-05-30〜）

**転換の理由:** これまでは bp 全体（3WF・4層・判断と配管が混在）を一度に流すビッグバン検証だった。要素が入り組み (1)どこを直すべきか1変数に絞れない (2)直すと別が壊れる。実際 ④を直したら②委譲非決定が露呈、と切り分け不能。→ 小さく作って各段で検証し積み上げる方式へ。

**目的（不変）:** CC・GHC両方で多層サブエージェントを動かす再利用可能な「方法」を見つける。

**最終成果物:**
1. 検証済み Example（bp。ドメインはトイで可・むしろ利点。topology が3パターンを網羅）
2. 3つのプロンプトパターン: ①サブエージェント呼び出し ②サブエージェントへの IN/OUT ③サブエージェントからのスクリプト呼び出し
3. 観測・検証の方法（transcript解析＋verify-run.py）＝ノウハウの第4の柱

**3層モデル（判断の所在）:**

| 層 | 種別 | 判断 |
|----|------|------|
| bp→SKILL移譲 | 配管 | 無し（作業指示通り） |
| SKILLルーティング | 分類 | 薄い判断 → 明示ルールで決定的に寄せる（唯一のグレー） |
| WF作業ステップ | 配管 | 無し（作業指示通り）★再現性の本丸 |
| サブエージェント | タスク | 本物のLLM判断（出力は非決定的でよい＝測らない） |
| filter-songs.sh | 計算 | 無し（コード＝決定的） |

**設計原則:**
- 判断はサブエージェント（層3）に閉じ込める。配管層（bp/SKILL/WF/IN-OUT/script）は判断ゼロの決定的指示。
- プロンプトは**基準1**を守る: 概要・ペルソナ・理由説明・判断動詞（analyze/select/Read and follow 等）を排し、**IN/OUT＋「書いてある通りやる」作業指示のみ**。
- 現プロンプト層（SKILL/WF/agent定義）は基準1違反のため**作り直す**。リソース（songs.json/members.json/filter-songs.sh）は検証済みなので**流用**。

**観測・検証方法（ノウハウ）:**
- CC: `~/.claude/projects/<proj>/<session>.jsonl`。tool_use(name+input)/tool_result。サブ起動=`Task` tool_use、サブ内部=`isSidechain`。
- GHC: `<workspaceStorage>/GitHub.copilot-chat/transcripts/<session>.jsonl`。tool.execution_start(toolName+arguments)。サブ起動=`runSubagent`、サブ内部=その start〜complete 窓の内側に入れ子。大OUT=`chat-session-resources/<session>/call_<id>/content.json`（call_id一致）。
- IN=起動プロンプト引数（JSON埋込判定に使用）。OUT=tool_result(CC)/content.json(GHC、completeは空のことあり要注意)。
- 計器: filter-songs.sh→`/tmp/bp-filter.log`。検証は貼り付けでなく transcript を読む（貼り付けは整形サマリで誤読の元）。
- **verify-run.py（未作成）**: transcript→層2不変条件をPASS/FAIL判定。A/B各段のテストハーネス。

**段階的タスクラダー:**
- [ ] **A. サブエージェント無し・メインのみで配管を再現性高く**（WFは後のサブ化を見越しファイル分割）
  - [x] A-1 CC OK（3WF×2=6/6 PASS, 2026-06-02） / [ ] A-2 GHC自動変換OK / [ ] A-3 CC/GHC両方OK
- [ ] **B. 1ステップずつサブエージェント化、各段で再現性確認**
  - [ ] B-1(1WF): CC→GHC自動変換→CC/GHC … [ ] B-2(2WF) … と積み上げ
- 各段で verify-run.py（or 手解析）で層2不変条件を検証してから次へ。Aもさらに細かく割ってよい。
- **旧フェーズ3（マッピング確定・設計書更新）はこのラダー完了後に回収。**

### A-1 測定結果（2026-06-02）★合格

**対象:** `/blackpink white` ×2（fresh CCセッション）。transcript: `07193a74`, `3c853b4d`。
**判定器:** `scripts/verify-run.py`（新規作成。transcript→層2不変条件をPASS/FAIL）。

| 不変条件 | run1 07193a74 | run2 3c853b4d |
|---------|---------------|---------------|
| 1. ルーティング white→optimized | PASS | PASS |
| 2. Step順 1→2→3（WF Read<filter<S1≤S2≤S3） | PASS | PASS |
| 3. filter-songs.sh 実行（bash参照＋/tmp/bp-filter.log） | PASS | PASS |
| 4. OUT形 S1/S2/S3（assistant本文＋tool_result両走査） | PASS | PASS |
| 5. 委譲なし（Stage A = Task 0） | PASS | PASS |

**所見:**
- 親は Step1/Step2 を **python one-liner の stdout** でJSON化（チャット本文には出さない）。よって OUT形検出は **tool_result も走査**が必須（verify-run.py はそうしている）。
- filter-songs.sh は `for tag in ...` ループ内実行のため **Bash tool_use 数 ≠ 実行回数**。実行回数は `/tmp/bp-filter.log` が正。
- **既知の計器限界:** bp-filter.log は共有グローバル。2ランが同分内のため in-window 切り分けは厳密でない（16/20・20/20）。check成立には影響なし（両run bash参照≥1・ログ≥1）。
- verify-run.py 初版の Step順チェックは assistant全文→tool_result全文の順で連結し位置が時系列とズレるバグ→**真の file 順で連結するよう修正済み**。

### A-1 測定結果: quick / versus（2026-06-02）★合格

quick.md / versus.md を optimized.md と同形のA版に再構築（commit c58603a）。各テーマを fresh CCセッション（`/clear`で各ラン分離）で2回ずつ測定。verify-run.py は**WFごとプロファイル**で判定（quick=S1/S3、versus=S1/S2/S3。versus の S2 マーカーは flow_score+recommended）。

| WF / theme | transcript | ルーティング | Step順 | filter | OUT形 | 委譲なし | 総合 |
|-----------|-----------|------------|--------|--------|-------|---------|------|
| quick `party` r1 | 972a33c9 | quick.md | S1≤S3 | ✓(3) | S1/S3 | Task0 | **PASS** |
| quick `party` r2 | f320c7d4 | quick.md | S1≤S3 | ✓(1) | S1/S3 | Task0 | **PASS** |
| versus `fierce vs emotional` r1 | 4080e7c1 | versus.md | S1≤S2≤S3 | ✓(3) | S1/S2/S3 | Task0 | **PASS** |
| versus `fierce vs emotional` r2 | 634ad1b9 | versus.md | S1≤S2≤S3 | ✓(2) | S1/S2/S3 | Task0 | **PASS** |

**所見:**
- 3WF×2ラン＝**6/6 全PASS**。A版（サブエージェント無し・親が全Step実行）はCCで層2が決定的に再現。
- versus は4ステップ（findA+findB→compare→show-plans）が層2通り。比較OUT（flow_score・recommended）も全ラン出現。
- **verify-run.py 修正:** `/clear` が直前に空 `<command-args>` を残すため、初版は theme を空取りして誤ルーティング判定→**command-name が /blackpink のブロックからのみ args 取得**するよう修正。これで quick/versus も正しく判定。
- 計器ログ in-window は連続実行で重なる（3/5/12/10 ÷ 30）が、bash参照≥1が主判定のため check3 成立に影響なし。

### 現在の作業状態（次セッションはここから）★RESUME

**A-1（CC）完了＝3WF×2ラン 6/6 全PASS（2026-06-02）。A-2 進行中: CC側A版をGHCへ自動変換し、GHCで同じ層2を再現させる。**

**A-2 進捗:**
- [x] **step1 完了（2026-06-02）** — 旧 big-bang 期のサブエージェント定義3つを**両プラットフォームから削除**（ユーザー承認＝選択肢1）。`.claude/agents/bp-*.md`（3）と `.github/agents/bp-*.agent.md`（3）を `git rm`。理由: A版はサブエージェント無し（不変条件5＝委譲なし）であり、旧agentは基準1違反（ペルソナ・判断動詞）でB段階の流用元にもならない。`python3 scripts/convert-cc-to-ghc.py` で `.github/` を再生成＝**agent無しのクリーンな8ファイル**（prompts/bp.prompt.md + skills配下SKILL/3WF/3resource）。`.claude/agents/` は消滅。A-1のCC合格は維持（削除は委譲の誘惑を減らすのみ）。
  - 注: `bp.prompt.md` の `tools:` に `agent` が残る（mapping-rules の add_fields。2-3で「prompt file には tools 必須」と実証済み）。stage Aでは未使用だが、実証済み要件のため温存。GHCが無委譲なら不変条件5はPASS、もし委譲したらそれ自体が知見。
- [ ] **step2 進行中** — verify-run.py に **GHCモード**を追加。GHC transcript の事実（下記「GHC transcript 形式」）に基づき実装。**OUT形の検出源だけが stage-A GHC で未確定**（端末stdoutは transcript に出ない／content.json は現状空）。→ 最初の実 stage-A GHC ラン1本で較正してから本実装を確定する方針。
- [ ] step3 — GHC（VS Code）で `/blackpink white` / `quick party setlist` / `fierce vs emotional` を各2回 → GHCモードで判定。
- [ ] step4 — 全PASS → A-3（CC/GHC両方OKの確認・差分文書化）。不合格 → 変換ルール or WF文言を1変数ずつ修正し再変換・再測定。

**GHC transcript 形式（2026-06-02 実ログ6本を解析して確定。`workspaceStorage/<ws>/GitHub.copilot-chat/transcripts/*.jsonl`）:**
- 1行=1イベント `{type, data, id, timestamp, parentId}`。
- `type`: `session.start` / `assistant.message`(data: content, toolRequests[], reasoningText) / `assistant.turn_start|turn_end` / `user.message`(data: content) / `tool.execution_start`(data: toolCallId, **toolName**, **arguments**) / `tool.execution_complete`(data: toolCallId, **success のみ＝出力なし**)。
- toolName 実測: `read_file`(args filePath/startLine/endLine)＝Read相当 / `run_in_terminal`(args command/explanation)＝Bash相当 / `runSubagent`(args prompt/description/agentName)＝**委譲マーカー** / `semantic_search`/`grep_search`/`file_search`/`list_dir`/`session_store_sql`/`manage_todo_list`。
- **CCとの差（重要）:**
  1. 親の元コマンド（`/blackpink white`）は **user.message として記録されない**（session.start 直後に assistant が応答）。→ GHCモードは theme を**`--theme` で受ける**（transcript から抽出不可）。
  2. 親はファイルを **`run_in_terminal` の `cat`** で読むことがある（read_file とは限らない）。→ WF読込検出は read_file の filePath と run_in_terminal の `cat .../workflows/<wf>.md` の**両方**を走査。
  3. **端末stdoutは transcript に出ない**（execution_complete は success のみ）。large OUT は本来 `chat-session-resources/<session>/call_<id>/content.json` だが**現状ディレクトリは空**。→ 信頼できるテキスト源は `assistant.message.content` のみ。OUT形マーカー（has_dance_break 等）は assistant 本文＋（在れば）content.json を走査。filter-songs 実行は **`/tmp/bp-filter.log`** が主証拠（端末stdout不在のため）。
  4. 委譲なし検出 = **runSubagent の出現数 0**（CCの Task==0 に相当）。

**測定の作法（A-1で確立）:** 各ランの前に `/clear`（コールド＆1ラン1transcript）。最初に `: > /tmp/bp-filter.log`。判定は transcript をツールで読む（貼り付け整形は誤読の元）。

**観測手順の詳細は本セクション「観測・検証方法」を参照。検証は貼り付けでなく transcript を読む。**


## タスク（旧・参考。前向きの計画は「## 新アプローチ」が正）

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
  - [x] **2-4b. 根本原因分析と分類** — 2026-05-29 完了（課題2）。原因=モデル一時可用性 (B)、ただし配列フォールバックで (A) 緩和可。詳細は「2-4b」参照
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
