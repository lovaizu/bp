# task-1 Completion Check

Scope of this self-check: **Step 2 only**（最小チェックスクリプト `scripts/check_transcript.py` の作成）。
実測ラン（CC・GHC コールドセッション各3回）と GHC パーサ対応は本 Step の範囲外・未実施。

## Completion Criteria

| Criterion | Self-check | Evidence | QA | QA Evidence |
|---|---|---|---|---|
| CC・GHC それぞれで3回中3回、`BPTRACE start`・Step1（非委譲、actor=main）・Step2（委譲、actor=techtest-echo）が正しく出力されていることが、チェックスクリプトの機械判定で確認されている（目視確認のみでは不可） | NG（未実施：実測待ち／本 Step の範囲外） | 測定はユーザー操作のコールドセッションでのみ行うルール（steering.md Rules）のため、本 Step では `/techtest` を実行していない。判定手段側の準備は完了：4条件（start 1回・Step1 非委譲・委譲が Step1 と Step2 の**間**・Step2 はサブエージェント自身の発話・委譲ちょうど1回）を機械判定でき、良／悪4パターンの transcript に対して `1/4 run(s) PASS -> FAIL` と正しく切り分けられることを実行で確認した（下記 Evidence 参照）。実測時の手順は**2段階**（発見と判定を分離）:<br>① 候補確認 `scripts/check_transcript.py --latest 5 --since <測定開始時刻> --dry-run`（採用N本・除外M本＋理由を表示、判定しない）<br>② 確定した3本をパスで明示して判定 `scripts/check_transcript.py run1.jsonl run2.jsonl run3.jsonl --expect 'start:theme=neon night,wf=techtest.md' --expect 'step=1:actor=main,origin=main' --expect 'delegate:to=techtest-echo' --expect 'step=2:actor=techtest-echo,origin=subagent:techtest-echo' --expect-count 'start=1' --expect-count 'delegate=1'` |  |  |
| 安定して機能した指示文パターンが記録されている | NG（未実施：実測待ち／本 Step の範囲外） | 実測していないため「安定したパターン」がまだ存在しない。`.claude/commands/techtest.md`・`.claude/agents/techtest-echo.md` は Step 1 で作成済み・本 Step では未変更 |  |  |
| チェックスクリプトが再利用可能な形（bp非依存の汎用部分）で残っている | OK | `scripts/check_transcript.py`（570 stmts、行・分岐とも 100%、175 テスト）。bp 固有・techtest 固有の期待値はスクリプト内に一切なく、すべて CLI から与える（techtest の4条件は docstring の Usage 例としてのみ記載）。既定動作は「抽出結果の報告のみ」（`REPORT ONLY`）。**判定層はプラットフォームのツール名を一切知らない**：パーサが `Event.kind` を `delegate`/`bash`/`tool`/`marker_*` に正規化し、`Expectation`・`evaluate` は kind と `origin` だけで判定する（デタラメなツール名を持つ合成 Event 列が正規化済み kind だけで一致することを `test_expectations_never_look_at_tool_names` で**振る舞いとして**固定。旧版のソース文字列 grep はトートロジーだったので置換）。プラットフォームは `Platform(name, parse, project_dir, transcripts)` を `PLATFORMS` に1件足すだけで載る（**ファイル列挙も Platform 側**なので `*.jsonl` 決め打ちが無く、発見層も含めて切り替わる。BPTRACE 共通仕様の `evidence_lines` / `marker_from_line` / `markers_in_text` / `finalize_run` は public な共通層に配置し、CC パーサ内部に手を伸ばさずに新パーサが書ける）。標準ライブラリのみ。実データの匿名化抜粋を `scripts/testdata/`（委譲なし／委譲あり＋サブ transcript＋meta／開発セッション）にコミットし、マシン非依存の必須テストにした |  |  |

### Evidence（本 Step の実データ確認）

**否定テスト（最重要）** — `~/.claude/projects/-home-tie303177-work-lovaizu-bp/2f444960-95ea-435c-b10a-86c620c91ecb.jsonl`（本開発セッション。設計書・steering.md を Read しただけで `/techtest`・`/bp` は未実行）:

- `grep -o BPTRACE ... | wc -l` = **70 件ヒット**（素朴な grep 実装なら誤検出する）
- `check_transcript.py` の判定 = **start markers: 0 / step markers: 0**（`--json` でも `start_markers: []`, `step_markers: []`）
- 内訳を自分で分解して確認（`type`／content block 種別ごとに集計）：この 70 件はすべて `user` の `tool_result`・`assistant` の `tool_use` の input・`attachment` に含まれるもので、`assistant` の `type=text` ブロックには 1 件も存在しない
- さらに全 transcript を横断して「`type=text` ブロック内に BPTRACE を含む」ファイルを調べたところ、開発セッション `8b70aa7e-...`・`9b92d297-...` が該当した。ただしそれらは `- §7.3を全面書き直し: マーカーの正確な文字列形式(...)` のように**行の途中**にマーカー文字列が現れる散文だった。そこで「マーカーは行全体でなければならない（strip 後の全体一致）」という規則を採用し、この2ファイルも 0 件になることを確認した（`techtest.md` の「output exactly this line」という指示と整合する）

**委譲なしランの現物突き合わせ** — `dc1addaf-3a1a-413c-b811-38002e93efd9.jsonl`（旧 `/bp` quick.md、D-6 で PASS と記録されているラン）:

- スクリプト出力: start 1件 `theme="give me a quick setlist" wf=quick.md`（L19）、step `1:main`（L28）・`2:main`（L31）、delegations 0、bash 2件（いずれも filter-songs.sh）
- 現物を独自スクリプトで JSON パースして行番号ごとに出力し、上記と 1 件ずつ一致することを目で確認（L12 Skill → L17 Read quick.md → L19 start → L20/L22 Bash → L28 step=1 → L29 Read members.json → L31 step=2）。イベント順序も一致

**委譲ありランでのサブエージェント統合** — `f71b8f11-...jsonl` / `908b6470-...jsonl`（D-6 で FAIL と記録されているラン）:

- `.meta.json` の `toolUseId`（`toolu_bdrk_012bGaVohob74gcvqM47P89d`）が親側 `Agent` tool_use の `id` と一致することを現物で確認し、その位置にサブエージェント transcript を差し込む実装にした
- スクリプト出力: delegations 1件 `-> bp-song-finder`（親 L22）、bash 8件（親 1 + `subagent:bp-song-finder` 7）。統合イベント列で subagent イベントが Agent 呼び出しの直後（seq 5 → 6..12）に並び、その後に親の L28 Read・L31 step markers（seq 13..15）が続くことを確認
- なお**この2本のサブエージェント transcript には BPTRACE 行が存在しない**（当時の `bp-song-finder.md` がマーカーを出さない定義だったため）。step markers は 2 件とも親側 `actor=main`
- 目視突き合わせ中に seq 番号が統合順と食い違う欠陥（親を先に通し番号付けしていたため 5 → 9..15 → 6,7,8 と表示された）を発見し修正、リグレッションテスト `test_merged_stream_is_numbered_in_display_order` を追加した。「判定だけ見て次に進まない」ルールが実際に効いた例

**判定経路の実データ実行** — `--latest 3 --theme 'give me a quick setlist'` を実プロジェクトディレクトリに対して実行し、stage-A の期待値（`start:wf=quick.md` / `--expect-count delegate=0`）を当てた結果:

```
dc1addaf : PASS
f71b8f11 : FAIL  x expected 0 x delegate, got 1; observed: [...] delegate -> bp-song-finder
908b6470 : FAIL  x expected 0 x delegate, got 1; observed: [...] delegate -> bp-song-finder
1/3 run(s) PASS -> FAIL   (exit code 1)
```

これは steering.md D-6 に人手で記録されている判定（dc1addaf は PASS、他2本は自発的委譲で FAIL）と独立に一致した。

**※ 上記の `--theme` 単独での絞り込みは、レビュー2巡目で「verdict 依存フィルタの別経路」として棄却された**（下記「レビュー2巡目」A 参照）。現在は `--theme` に一致しなかった候補も**除外として理由つきで報告**され、期待値がある場合は FAIL になる。実測は「①`--dry-run` で候補確認 → ②確定した3本をパスで明示」の2段階手順で行う。

### 3人の敵対的レビュー（QA / Craft / Verification）を受けた修正

3人全員 FAIL。16件の指摘（V1〜V16）を TDD で修正した。判定を誤 PASS させうる経路が実際に4つあった:

- **V1 誤 PASS を再現→修正確認**: `--latest 3` の候補選別が「start マーカーを持つファイル」に限定されていたため、**直近ランがマーカーを出し忘れると、そのランが候補から静かに消えて古いランで埋められた**。4本（新しい順に「マーカー無し・良・良・良」）で再現: 修正前 `3/3 run(s) PASS -> PASS`（exit 0、マーカー無しの最新ランは出力に一切現れない）／修正後 `2/3 run(s) PASS -> FAIL`（exit 1、最新ランが `FAIL` として先頭に出る）。選別は verdict を見ず、`--theme`（完全一致）と `--since` で絞る方式に変えた
- **V3 誤 PASS を再現→修正確認**: assistant がコードフェンス内でマーカーを引用しただけの討議セッションが本物のランとして PASS していた。修正前 `1/1 run(s) PASS -> PASS`（exit 0）／修正後 `0/1 run(s) PASS -> FAIL`（exit 1、`start markers : 0`）。フェンス（``` / ~~~）区間をブロック単位の状態機械で除外。**フェンスの「後」に出るマーカーは従来どおり有効**であることを実データ `dc1addaf` L28（fenced JSON の直後に `BPTRACE step=1`）で確認済み
- **V2（本丸）**: `origin` を全セレクタ共通キーにした（完全一致）。実データ `f71b8f11` に `step=1:actor=main,origin=subagent:bp-song-finder` を当てると FAIL、`origin=main` なら PASS。**委譲があったランでも step マーカーは親が出していた**ことを機械判定で示せるようになった（従来は表現不能）
- **V4**: 書式を外した `BPTRACE` 行（太字化・引用符・末尾ピリオド・theme 内の `"`）を `marker_malformed` として記録し anomaly に載せる。`actor` は `[A-Za-z0-9_-]+` に絞ったので `actor=main.` を誤マッチしない
- **V5**: 期待値がある場合、anomaly があれば既定で FAIL（`--allow-anomalies` で従来動作）
- **V8**: 入れ子委譲を再帰 splice（循環防止付き）。実データ（`spawnDepth` 2 の meta が実在するセッション）で **57 イベントが従来は無警告で落ちていた**ことを確認
- **V10**: `cc_project_dir` のスラグを「非英数字→`-`」に修正。`/home/.../dev/.claude-worktrees/ai-quality` から導出したパスが実ディスク上のディレクトリと一致することを確認（従来は worktree 配下で exit 2）
- **V6/V11/V12**: `.meta.json` が dict 以外／読めない／transcript 欠落を warning に降格し理由を残す。`main()` にトップレベル捕捉を置き、クラッシュは必ず exit 2（PASS=0 / FAIL=1 / エラー=2）
- **V7**: 実 transcript の匿名化・最小化抜粋を `scripts/testdata/` にコミットし必須テスト化。ライブセッション依存を撤廃（マシン依存テストは `BP_LIVE_CC_PROJECT_DIR` 指定時のみの別建て）
- **V16**: `scripts/check-transcript.py` → `scripts/check_transcript.py` にリネーム（テスト側の `importlib` 回避コードを削除）。`.gitignore` に `__pycache__/` 追加

**stage-B 4条件の1コマンド表現を実行で確認** — 良1本＋悪3本（Step1 で委譲／委譲なし／親がサブのマーカー行を転記）に上表のコマンドを当てた結果 `1/4 run(s) PASS -> FAIL`。特に「親が転記しただけ」のランは `origin` 修正前には検出不能だった。

**エッジケース**（テストで固定）: 存在しないパス（exit 2）、読めないファイル、壊れた JSON 行、空ファイル、マーカーなしファイル、`--latest N` の不足（`1/3 run(s) PASS (2 missing) -> FAIL` と整合表示）、`content` が list でない assistant イベント、非 dict content block、`.meta.json` の破損／非 dict／`toolUseId` 欠落／transcript 欠落、subagent transcript が無い `Agent` 呼び出し、start マーカー 0件／複数件、順序違反、`--expect` 文法（負数・非 ASCII 数字・`step=001`・キー重複・位置引数と `--project-dir` の併用）、探索中に読めないファイル／消えたファイル。

### レビュー2巡目（3人とも再 FAIL）を受けた修正

1巡目の修正後も 123 passed / 行・分岐 100% を**すり抜けた**欠陥が 12 件（A〜M）。カバレッジは品質の証明にならないことの実例なので、各件を「修正前は誤判定 → 修正後は正しい判定」で再現確認した。

- **A [Critical] `--theme` が verdict 依存フィルタだった（V1 の別経路での再発）** — 実データ再現: 修正前 `--latest 3 --repo . --theme 'give me a quick setlist' --expect start` が 2026-07-24 の3本を選び `3/3 run(s) PASS -> PASS`（exit 0）。それ以降の4本（07-27 の2本を含む）は無言でスキップされ、標本が3日前であることの警告もゼロ。修正後は同じコマンドが**除外した4本をパスと理由つきで列挙**し `3/3 run(s) PASS -> FAIL`（exit 1）。誤挙動を仕様として固定していたテスト `test_latest_selects_by_exact_theme`・`test_cli_theme_scopes_the_measurement` は正しい期待に書き換えた
- **B [Critical] 発見層の warning が verdict に効かなかった** — 実データ（実 transcript を複製して最新1本を chmod 000）再現: 修正前 `--latest 2 --expect start --allow-anomalies` が `! skipped newest.jsonl: ...` と出しつつ `2/2 run(s) PASS -> PASS`（exit 0）。修正後 `x excluded newest.jsonl: cannot be read (...)` → `2/2 run(s) PASS -> FAIL`（exit 1）。`stat()` 失敗も warning すら出ていなかったので除外として報告するようにし、黙殺を固定していた `test_a_transcript_that_vanishes_mid_search_is_skipped` を書き換えた
- **A・B の構造的修正** — 「どの transcript が測定なのか」を推測させない設計に変更。①複数パスの位置引数が**判定の第一級の入り口**（明示したランは1本残らず判定、読めなければ FAIL）②`--latest`/`--theme`/`--since` は発見の補助で、**マーカーの有無で候補を落とさない**（theme 不一致も「除外＋理由の報告」）③期待値があるとき shortfall・除外・読み取り失敗のいずれかがあれば FAIL（`--allow-anomalies` とは別扱い）④採用N本と除外M本（理由つき）を常時表示 ⑤`--dry-run` で選択だけ確認。`--since` だけは**窓**（窓外は候補ですらない）として除外に数えない
- **C [Critical] フェンス除外の穴4種** — 4スペースインデント／リスト項目内フェンス（`- ```）／入れ子フェンス（`` ``` `` の中の ` ```json ` の閉じが外側を閉じたと誤判定。`techtest-echo.md` の OUT 節がこの形）／HTML コメント。修正前はいずれも**証拠として計上**、修正後はいずれも `marker_quoted`（＝証拠にしない）。フェンスは strip 前の行に対して `^ {0,3}` ＋リストマーカー接頭辞で検出し、info 文字列つきフェンスは入れ子として push、同じ文字種・同じ長さ以上の裸フェンスだけが pop する
- **C' [High] フェンス内の正規形マーカーを黙って捨てていた** — `marker_quoted` として記録し anomaly に載せる（「証拠にしない」と「無かったことにする」を分離）。フェンス内の**壊れた**行はノイズなので従来どおり捨てる
- **D [High] malformed の過検出** — 実データ `9b92d297-...jsonl` の日本語散文2行（`BPTRACEマーカーは、この誤判定を防ぐための**固定トークン**です。…` と `` - `BPTRACE` マーカーが正しく出力されるか ``）が malformed → anomaly → FAIL になっていた。near-miss を `BPTRACE\s+(?:start|step)\b` に限定し、修正前 malformed 2件 → 修正後 0件。全実 transcript を新旧で突き合わせ、判定が変わったのはこの1本だけであることを確認
- **E [Medium] 全角数字の step** — `MARKER_STEP_RE` を `[0-9]+` にし、`BPTRACE step=１ out actor=main` は malformed（従来は `step='１'` の正規マーカー）
- **F [Medium] 発見層が CC 専用だった** — `Platform` にファイル列挙（`transcripts`）を持たせ、`find_runs` の `parser` からデフォルト値を削除（忘れた呼び出しは `TypeError`）。`.log` を読む架空プラットフォームで発見層が動くことをテストで固定
- **G [Medium] exit code 契約** — `parse_args` を try の内側へ（想定外例外が Python 既定の exit 1＝FAIL になっていた）。`BrokenPipeError` を明示捕捉：実データで `check_transcript.py <file> --verbose | head -1` が **exit 2 + `internal error: BrokenPipeError` → exit 0** に
- **H [Medium] 既定レポートの肥大** — 実データ最大で **3601 行 → 33 行**（`9b92d297`: 545 行 52KB → 35 行 3.2KB）。カテゴリ別一覧は10件で打ち切り（`--verbose` で全出し）、`describe()` は改行を `⏎` に畳んで 160 字で切る
- **I [Low] 期待値パーサ** — 値中のカンマにエスケープ（`\,` / `\\`）を導入し末尾の裸バックスラッシュは UsageError、空の `contains` を拒否、`agentType` 欠落時の origin を `subagent:subagent` から `subagent:<unknown>` へ
- **J [Low] 型注釈** — `Optional[int]`・`Callable[...]`・`list[Event]`・`dict[str, str]` 等（`object` は「呼べない型」の宣言だった）
- **K [Low] docstring** — `r"""` 化（例の行末 `\` が `--help` で消えていた）、設計判断の論争的な記述を削除して不変条件の記述に絞る
- **L [Low] テスト品質** — 書き換えたテスト: `test_cli_exits_2_when_an_unexpected_exception_escapes`（名前と中身が正反対・`returncode in (0,1)` の選言）→ `test_cli_treats_a_non_object_meta_json_as_a_failing_anomaly_not_a_crash`（`== 1`）／`test_origin_catches_a_marker_the_parent_merely_transcribed`（恒真アサート）→ 実際に観測された origin `[main ` を見る／`test_expectations_never_look_at_tool_names`（ソース grep）→ 振る舞いテスト／`test_since_accepts_an_iso8601_string`（到達しただけ）→ `Z`＝1785110400.0・`+09:00`＝1785078000.0 と既知値突き合わせ＋naive はローカル TZ／`test_latest_selects_by_exact_theme`・`test_cli_theme_scopes_the_measurement`・`test_a_transcript_that_vanishes_mid_search_is_skipped`（A・B の誤挙動を固定）→ 正しい期待へ。`--allow-anomalies` の CLI 経由テストを3本追加、`agent-aaa.meta.json` のハードコード5箇所を `happy_meta()`/`happy_sub()` に集約
- **M [Low] フィクスチャ** — `scripts/testdata/cc-dev-session.jsonl` に C の2形（4スペースインデント／リスト項目内フェンス）と D の日本語散文行を追加し、「証拠にならない／malformed にならない／quoted としては記録される」ことを固定

**今回スコープ外**（指示による）: `origin=subagent:*` の実データ由来フィクスチャ（`actor=techtest-echo` の実ランがまだ存在しないため、測定後に匿名化して追加）、GHC パーサ本体の実装。

**カバレッジ**: `coverage`（`--branch`、subprocess 実行の CLI テストも `COVERAGE_PROCESS_START` で収集）。
`scripts/check_transcript.py`: **570 stmts / 0 miss（行 100%）、228 branch / 0 partial（分岐 100%）**。
175 passed, 1 skipped（skip はマシン依存の opt-in テストのみ。必須テストは全て `scripts/testdata/` ベースで環境非依存）。
**ただしカバレッジは品質の証明ではない**：今回の A〜M は全て 100% をすり抜けた。アサーションが弱い箇所（恒真・選言・到達確認のみ）を自分で探して L で潰した。

## QA Expert Review

| Aspect | Verdict | Evidence / Improvement |
|---|---|---|
| Verification approach meaningful to the objective (checks the right thing, not just "passed") |  |  |

## Expert Reviews (axes the task needs)

### Craft Expert (coding)

| Aspect | Verdict | Evidence / Improvement |
|---|---|---|
| Medium-specific best practice |  |  |
| Consistency with existing style |  |  |

### Verification Expert (test)

| Aspect | Verdict | Evidence / Improvement |
|---|---|---|
| Artifact actually checked (tests run / claims verified / flow traced) |  |  |
| Coverage (edge cases / claims / steps) |  |  |

## Overall Verdict

- Self-check: NG（task #1 全体としては未達。3つ目の criterion「チェックスクリプトが再利用可能な形で残っている」= OK、1つ目・2つ目は実測未実施のため NG。本 Step（Step 2）は、3人の敵対的レビュー1巡目の V1〜V16 に続き、2巡目の A〜M を全て修正して完了。2巡目では発見層を構造的に作り直した（判定の第一級の入り口を「複数パスの位置引数」にし、`--latest`/`--theme`/`--since` は発見の補助に降格。マーカーの有無で候補を落とさず、除外は理由つきで必ず報告し、期待値があるときは除外・shortfall・読み取り失敗のいずれも FAIL）。critical の A・B、および C・C'・D は実データで「修正前は誤判定（うち A・B は exit 0 の誤 PASS）／修正後は正しい判定」を再現して確認した。判定手段が信用できる状態になったので、次は実測に進める）
- QA:
- Craft expert:
- Verification expert:
- Ready to check off:
