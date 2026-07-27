Rn version: 0.8.0

# Goal

CC（Claude Code）とGHC（GitHub Copilot）の両方でメインエージェント＞サブエージェント構成を動かす「方法」を見つけ、再利用可能な形に確立する。

CCで設計したメインエージェント＞サブエージェント構成をマッピングルールでGHCに機械変換し、両プラットフォームでサブエージェントが動くことを実証する。成果物は ①動作する両プラットフォーム実装 ②マッピングルール／変換スクリプト ③方法とプラットフォーム差（サブエージェントを動かす勘所）の文書化。

BLACKPINKセットリスト・プランナーは検証用サンプル。プランナー自体も「同等の出力」も目的ではなく、「方法が両プラットフォームで動くか」を確かめる手段。

# Acceptance criteria

- CC・GHC 両方で `/bp <theme>` 呼び出しが layer-2 不変条件（ルーティング・Step順・filter実行・OUT形・委譲有無）を `scripts/verify-run.py` で PASS する
- 段階的ビルドアップの各段（A→B）で再現性が成立する（前段の PASS を後段で壊さない）
- プロンプトパターン3種（①サブエージェント呼び出し ②サブエージェントへの IN/OUT ③サブエージェントからのスクリプト呼び出し）が両プラットフォームで動くことを事実で示せる
- CC↔GHC の差分が「実装の工夫で解決したもの」と「プラットフォーム固有の制約として残るもの」に分類・文書化されている
- 設計書（`docs/cross-platform-agent-design.md`）が検証結果・マッピングルール・制約を反映した最終版になっている

# Assumptions

- GHC のデフォルトモデルが CC と比較して著しく性能差がある場合、CC/GHC 差はプラットフォーム差でなくモデル差になるため同等モデル指定が必要（未確認: GHC で Opus 4.x が選べるか）
- GHC はセッション最後の応答ターンのみ transcript に記録しない（`docs/cross-platform-agent-design.md` §3.1）。委譲先ステップの OUT はこの制約を受けない
- `bp.prompt.md` の `tools: agent` は stage-A では未使用だが実証済み要件のため温存

# Rules

- 1 task = 1 commit
- 推測せず事実ベースで判断する（不確かな点は「未確認」と明記する）
- 測定は必ずユーザー操作のコールドセッションで行う（このセッション内で `/bp` を起動して測定してはいけない）
- 測定後の transcript ID はユーザーに聞かない。`scripts/check_transcript.py --latest N` で自動検索する。ただし判定は「①`--latest`/`--since`/`--dry-run` で候補を確認 → ②確定したパスを明示指定して判定」の2段階で行う（`--latest` 単独に判定を委ねない）
- filter-songs.sh の証拠ログ `/tmp/bp-filter.log` はリセットしない（貯めっぱなし運用・時間窓で切り分け）
- 1変数ずつ変えて再測定する（ビッグバン検証は行わない）
- GHC transcript は最終OUT生成ターンを欠く → 画面出力も証拠として保存する
- ファイルを変更したらコミット後に必ず push する
- `verify-run.py` を修正・信頼する前に、実際のtranscript内容（現物）とチェッカーの判定根拠を必ず突き合わせて確認する。判定だけを見て次に進まない（根拠: WFファイル・members.json等のリソースファイル読込テキストがマーカー検索に混入し、実際には未検証のまま「PASS」としていたことが複数回発覚した）
- ユーザーへの回答は結論と根拠を先に述べる。詳細説明はユーザーから聞かれてから答える（ダラダラと経緯から書かない）

# Tasks

### #1: 技術検証 — マーカー・委譲・非委譲の再現性を最小構成で確認する（bp非依存）

**Purpose**: BPTRACE マーカー出力・明示的委譲・明示的非委譲（委譲可能なサブエージェントが存在する状態での）が、CC・GHC それぞれで実際に安定して機能するかを、BLACKPINK と無関係な最小構成で実測確認する。

**Prerequisites**: none

**Steps**:

- [x] CC用の最小検証コマンド（1WF・2ステップ：Step1は非委譲・Step2は委譲）とサブエージェント1つを作成する（`/techtest`）
- [x] 最小チェックスクリプト（`scripts/check_transcript.py`）を作成する。CC transcript を読み、`BPTRACE start`／`BPTRACE step=<n> out actor=<...>` 行と Agent tool_use 呼び出し回数を機械的に抽出・報告する（bp非依存、汎用。目視・都度のワンライナーで確認しない）
- [ ] 3ラウンド目の修正（偽PASS/偽FAIL 12件）を検証し、レビューを1巡させる。**修正はワーキングツリーに存在するがコミット・検証・レビューとも未了**（詳細は State → Notes）
- [ ] CC: コールドセッションで3回実行し、チェックスクリプトで `BPTRACE start`・Step1（非委譲・actor=main）・Step2（委譲・actor=techtest-echo）が3/3で正しく成立するか確認する
- [ ] 3/3で安定しなければ、指示文を1変数ずつ修正し再測定する
- [ ] 安定したパターンを GHC へ変換し、GHC でも同様に3回、チェックスクリプト（GHC transcript 対応を追加）で確認する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-1.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, writing)
- [ ] Verification expert review (subagent, dry-run — 実測 transcript でのトレース)

**Completion criteria**:

- CC・GHC それぞれで3回中3回、`BPTRACE start`・Step1（非委譲、actor=main）・Step2（委譲、actor=techtest-echo）が正しく出力されていることが、チェックスクリプトの機械判定で確認されている（目視確認のみでは不可）
- 安定して機能した指示文パターンが記録されている
- チェックスクリプトが再利用可能な形（bp非依存の汎用部分）で残っている

---

### #2: 検証済みパターンで bp を1WF・非委譲で構築し安定させる

**Purpose**: task #1 で確定したパターンを使い、BLACKPINK セットリスト・プランナーを1つの WF（サブエージェントなし、CC のみ）として実装し、チェックスクリプトで実測確認して安定させる。

**Prerequisites**: #1

**Steps**:

- [ ] 実測後、`origin=subagent:*` を含む実データ由来の匿名化フィクスチャを `scripts/testdata/` に追加する（task #1 時点では実ランが無く合成フィクスチャのみで担保していたため）
- [ ] チェックスクリプトを拡張する: ルーティング（該当WFを読んだか）・OUT形（想定フィールドの有無）の判定を追加する（bp固有のロジックはここで初めて入る）
- [ ] `/bp` コマンド・1つの WF（`songs.json`/`members.json`/`filter-songs.sh` を使い、テーマから楽曲選定→並び替え→演出プラン生成まで一気通貫、サブエージェントなし）を実装する。task #1 で確定したマーカーパターンを適用する
- [ ] CC でコールドセッションで複数回実測し、チェックスクリプトで安定を確認する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-2.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, writing)
- [ ] Verification expert review (subagent, dry-run)

**Completion criteria**:

- 1 WF（サブエージェントなし）が実測で複数回連続して安定動作することが、チェックスクリプトの機械判定で確認されている

---

### #3: 一部をサブエージェント化し、その状態で安定させる

**Purpose**: task #2 で安定した1 WF のうち1ステップ（楽曲検索）をサブエージェント委譲に変更し、チェックスクリプトで実測確認して安定させる。

**Prerequisites**: #2

**Steps**:

- [ ] チェックスクリプトを拡張する: 委譲有無・actor一致の判定を追加する
- [ ] 楽曲検索ステップをサブエージェント（`bp-song-finder` 相当）に切り出す
- [ ] CC でコールドセッションで複数回実測し、チェックスクリプトで委譲が安定して発生し、非委譲ステップに影響しないことを確認する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-3.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, writing)
- [ ] Verification expert review (subagent, dry-run)

**Completion criteria**:

- サブエージェント委譲を含む WF が実測で複数回連続して安定動作することが、チェックスクリプトの機械判定で確認されている

---

### #4: GHC へ移植し、同様に安定させる

**Purpose**: task #2・#3 で確定した CC 実装を GHC へ機械変換し、チェックスクリプトで GHC でも同様に安定動作することを実測確認する。

**Prerequisites**: #3

**Steps**:

- [ ] チェックスクリプトを拡張する: GHC transcript の解析（主ログ＋ runSubagent 境界＋ content.txt の統合）に対応する
- [ ] 変換ルール／スクリプトを（必要なら再実装して）適用する
- [ ] GHC でコールドセッションで複数回実測し、チェックスクリプトで CC と同等の安定動作を確認する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-4.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, coding)
- [ ] Verification expert review (subagent, test)

**Completion criteria**:

- GHC でも CC 同様に複数回連続して安定動作することが、チェックスクリプトの機械判定で確認されている
- この時点でチェックスクリプトは CC・GHC 両対応、§7.3 の C1–C5 相当を実データ（SYNTHETIC フィクスチャではなく）で判定できる

---

### #5: 必要ならプロンプトパターンのバリエーション（quick/versus 等）を追加する

**Purpose**: Acceptance criteria の「プロンプトパターン3種」を満たすため、task #2–#4 で確定した安定パターンを使い、追加の WF バリエーションを実装・安定させる。

**Prerequisites**: #4

**Steps**:

- [ ] チェックスクリプトを拡張する: 複数WF存在時のルーティング判定・stage-A測定時の非委譲確認を追加する
- [ ] 追加パターン（quick/versus 等）を、確定済みパターン（非委譲ステップへの明示禁止指示を含む）で実装する
- [ ] CC・GHC で複数回実測し、チェックスクリプトで安定を確認する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-5.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, writing)
- [ ] Verification expert review (subagent, dry-run)

**Completion criteria**:

- 追加パターンが CC・GHC 双方で複数回連続して安定動作することが、チェックスクリプトの機械判定で確認されている

---

### #6: CC・GHC で全 WF・全 stage を本測定する

**Purpose**: task #1–#5 で安定を確認した全構成を対象に、チェックスクリプトで CC・GHC 両方の layer-2 不変条件の PASS を本測定として確認する。

**Prerequisites**: #5

**Steps**:

- [ ] CC: 全 WF を各 stage で複数ラン測定し、チェックスクリプトで判定する
- [ ] GHC: 全 WF を各 stage で複数ラン測定し、チェックスクリプトで判定する
- [ ] FAIL が出た場合、原因を1変数で特定し修正して再測定する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-6.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, coding)
- [ ] Verification expert review (subagent, test)

**Completion criteria**:

- CC・GHC とも全 WF・全 stage が PASS している（または FAIL 原因が1変数で特定済み）
- 各ランの transcript ID と判定結果が記録されている

---

### #7: CC/GHC 差分を文書化する

**Purpose**: 測定結果をもとに、CC/GHC の差分を「実装の工夫で解決したもの」と「プラットフォーム固有の制約として残るもの」に分類し、設計書に反映する。

**Prerequisites**: #6

**Steps**:

- [ ] 差分を分類する
- [ ] `docs/cross-platform-agent-design.md` §9 に測定結果を反映する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-7.md)
- [ ] QA expert review (subagent)
- [ ] Craft expert review (subagent, writing)
- [ ] Verification expert review (subagent, fact-check)

**Completion criteria**:

- 差分が「解決済み」と「残存制約」に分類されて設計書に記録されている
- 設計書が検証結果を反映した最終版になっている

# Decisions

## D-5: タスク分割を「成果物単位」から「リスク優先の実測検証」へ組み替え（2026-07-24）
- **Issue**: 旧タスク構成（#1マーカー実装→#2チェッカー実装→#3実測→#4文書化）は、一番のリスク（LLMがBPTRACEマーカーを実際に安定出力するか）を検証しないまま、#1・#2という重い実装・フルレビュー工程を先に完了させていた。D-1（段階的ビルドアップ・1変数ずつ）の原則に反する分割だった
- **Conclusion**: 旧#1・#2は完了記録として残す（実装自体は無駄ではない）が、旧#3・#4を差し替え、新たに「#3: 実測でマーカー再現性を検証（quick.md・CC→GHCの最小構成、軽量レビュー）」「#4: verify-run.pyを実データで検証」を挿入。全WF本実測（新#5）・文書化（新#6）はその後に続ける
- **Rationale**: 一番リスクの高い前提（マーカーがLLMの自然言語指示への追従で安定出力されるか）を最小コストで先に検証することで、方式自体に問題があった場合の手戻り範囲を最小化する
- **Evidence**: 本セッションでユーザーから「目的単位でタスクを組んでいない、破綻している」との指摘。旧#2の完了条件「現物と突き合わせた動作確認」がSYNTHETICフィクスチャのみで満たされておらず、実質的に未達のままチェックオフされていたことが発端
- **Sources**: 本会話

## D-1: ビッグバン検証から段階的ビルドアップへ転換（2026-05-30）
- **Issue**: 要素が入り組み、直すべき箇所を1変数に絞れない・直すと別が壊れる状況が続いた
- **Conclusion**: 段階的ビルドアップに移行。サブエージェント無しの配管確認（A段階）→ 1ステップずつサブエージェント化（B段階）の順で積み上げる
- **Rationale**: 1変数ずつ変えることで原因特定が可能になり、各段で再現性を確認してから次へ進める
- **Evidence**: 2-4c で④（JSON受け渡し）を直したら②（委譲非決定性）が露呈し、課題が連鎖して切り分け不能になった
- **Sources**: `docs/steering.md` §2-4c 改善方針・§新アプローチ

## D-2: 親仲介でのJSON受け渡しを採用（2026-05-30）
- **Issue**: サブエージェント間のJSONを「ファイル側チャネル」で受け渡すか「親仲介」にするか
- **Conclusion**: 親仲介を採用。WF の受け渡し指示を強制力UP（隔離理由明記・verbatim 貼付命令）
- **Rationale**: 両プラットフォームともサブエージェントは隔離コンテキスト。ファイル側チャネルは隔離モデルを壊す抜け道で真因を直さない
- **Evidence**: CC公式・GHC公式ともに「subagentはchatを知らず、chatもsubagentを知らない」と明記。データは親仲介が唯一の正攻法
- **Sources**: CC: code.claude.com/docs/en/sub-agents, GHC: code.visualstudio.com/docs/copilot/agents/subagents

## D-3: 旧サブエージェント定義の削除（2026-06-02）— **D-6 により無効化（2026-07-24）**
- **Issue**: A版（サブエージェント無し）の測定中に旧 big-bang サブエージェント定義3つが残存していた
- **Conclusion**: `.claude/agents/bp-*.md`（3）と `.github/agents/bp-*.agent.md`（3）を git rm し、A版クリーン環境に
- **Rationale**: A版の不変条件5（委譲なし）を正しく測定するために旧定義は除去が必要。B段階の流用元にもなれない（基準1違反：ペルソナ・判断動詞を含む）
- **Evidence**: A-1測定でA版はTask0（委譲なし）が確認済み
- **Sources**: commit 6b1d4f2

## D-4: CC・GHC ともに Sonnet 4.6 で測定（2026-06-17 更新）
- **Issue**: GHC 初回6本は GPT mini/Haiku レベル（小型モデル）で実行されていた可能性が高く、指示追従不足がプラットフォーム差と分離不能だった
- **Conclusion**: CC・GHC ともに Sonnet 4.6 で測定する。CC Opus 4.8 の既存6本は無効（異モデル）
- **Rationale**: 目的はプラットフォーム差の検証。Sonnet 4.6 で揃えることで同一モデル比較が可能。CC Opus 4.8 結果は別モデルの測定であり流用不可
- **Evidence**: 初回「Sonnet 以上であれば CC と GHC で同等」（2026-06-17）→ versus 再測定時に「今回はCC も Sonnet 4.6」と確認（2026-06-17）
- **Sources**: 本会話

## D-6: stage-A WFへの明示的な非委譲指示追加。D-3を無効化（2026-07-24）
- **Issue**: task #3 実測（CC・quick.md 3回）で、2/3回 `bp-song-finder` への自発的委譲が発生（C5 FAIL）。原因は `.claude/agents/bp-song-finder.md` が常時存在し、CCのAgentツールがWFの指示と無関係に全定義済みサブエージェントを選択可能にしているため。D-3は「stage-A測定中はサブエージェント定義ファイルを削除する」としていたが、D-1以降の段階的ビルドアップで `optimized.md` が `bp-song-finder` に恒久的に依存する構成になり、もはやファイルを削除できない。D-3は設計変更後に見直されず、無効な前提のまま残っていた
- **Conclusion**: D-3を無効化。`quick.md`／`versus.md`（CC・GHC 両方）のStep 1に「Agentツール・いかなるサブエージェント（bp-song-finderを含む）も使わず、filter-songs.shを自分で直接実行しろ」という明示の禁止指示を追加した
- **Rationale**: ファイルレベルでの分離が維持できない以上、委譲の防止も委譲の実行と同様にプロンプト指示に頼るしかない（§3の非決定性は防止側にも対称的に適用される）。指示が皆無だった状態から明示指示ありに変えることで、遵守率の改善を期待し、再実測で確認する
- **Evidence**: `dc1addaf-...jsonl`（PASS、委譲なし）、`f71b8f11-...jsonl`／`908b6470-...jsonl`（FAIL、`bp-song-finder`への自発的委譲を確認、Agent tool_use の `input` に filter-songs.sh 相当の作業を委譲するプロンプトが記録されている）
- **Sources**: 本会話、task #3 実測ラン3本

## D-7: 実装を全消去し、技術検証から段階的に作り直す（2026-07-24）— D-5 のタスク構成を無効化

- **Issue**: D-5 の組み替え後も、`quick.md`/`versus.md`/`optimized.md`/`bp-song-finder.md` を最初から並行して作ってしまっていたため、stage-A（非委譲）と stage-B（委譲）が同一リポジトリに同時存在する構造的矛盾（D-6参照）が起きた。対症療法（D-6の指示文追加）を重ねるたびに新しい落とし穴が見つかり、ユーザーから「どこでハマるか分からず怖い」との指摘があった
- **Conclusion**: 実装済みファイル全て（WF・エージェント定義・`verify-run.py` 一式・変換スクリプト・checks記録、CC/GHC 両方）を削除し、設計書（`docs/cross-platform-agent-design.md`）とリソースデータ（`songs.json`/`members.json`/`filter-songs.sh`）のみを残して最初からやり直す。新タスク構成: #1 技術検証（bp非依存の最小構成でマーカー・委譲・非委譲の再現性を確認）→ #2 bpを1WF・非委譲で構築・安定 → #3 一部をサブエージェント化・安定 → #4 GHC移植・安定 → #5 追加WFバリエーション → #6 全WF・全stage本測定 → #7 文書化。D-5 のタスク構成（旧#3〜#6）は無効化。チェックスクリプトを単独タスク化せず各タスクに分散統合したのは D-8 参照
- **Rationale**: 各段階で存在するWFを1つに保てば、「委譲しないはずのWFが別WF用のサブエージェントを誤って呼ぶ」という構造的矛盾自体が発生しない（D-1の段階的ビルドアップの原則をタスク構成だけでなく成果物の存在範囲にも適用する）。技術検証をbpと切り離すことで、bpの仕様（設計書に記録済み）を汚さずに委譲・非委譲の指示文パターンだけを安く確定できる
- **Evidence**: task #3 実測で bp-song-finder への自発的委譲が発覚 → D-6 で指示文修正 → ユーザーから「なんでそんな状態で測定するの」「全部最初からやり直して」との指摘。git履歴は保持（スカッシュはリスクが高いため見送り、通常コミットでの削除に留めた）
- **Sources**: 本会話

## D-8: チェックスクリプトを単独タスク化せず、各タスクに分散統合する（2026-07-24）

- **Issue**: D-7 のタスク構成でも、task #2〜#4（bp構築・サブエージェント化・GHC移植）は目視確認のまま進め、チェックスクリプト（旧 `verify-run.py` 相当）の実装は task #5 まで後回しにしていた。ユーザーから「#2以降は測定して、チェックスクリプトでチェックしないのか」「先にスクリプトを作って測定できる状態にしてから実装・測定では」との指摘があった
- **Conclusion**: 単独の「チェックスクリプト実装タスク」を廃止。task #1 でまず最小のチェックスクリプト（bp非依存、`BPTRACE`＋Agent呼び出し検出のみ）を先に作り、task #2〜#5 それぞれが「その段階で必要な判定ロジックをチェックスクリプトに追加してから実装・実測する」という順序に変更した（8タスク→7タスクに統合）
- **Rationale**: 目視確認は再現性がなく、これまで繰り返し問題を起こしてきた検証方法そのもの。各段階で「先に測る手段を用意してから作る」ことで、段階的ビルドアップの原則を検証手段にも一貫して適用する
- **Evidence**: 本会話でのユーザー指摘
- **Sources**: 本会話

## D-9: チェックスクリプトの判定原則（2026-07-27）

- **Issue**: task #1 のチェックスクリプトに対する3巡の敵対的レビューで、「実際には成立していない実行を PASS と判定する」経路が**毎回新しく**見つかった。過去の事故（リソースファイル読込テキストの混入）と同型の欠陥が、混入元を変えて繰り返し出現した
- **Conclusion**: 次の原則をスクリプトの不変条件として確立した。(1) **選別は verdict を見ない** — 「start マーカーの有無」で候補を絞ると、まさに捕まえるべきラン（モデルが出し忘れたラン）が落ち、古いランで静かに埋められる。除外した候補は必ず理由つきで報告し、期待値がある場合は除外・欠落があれば FAIL。(2) **判定は2段階** — 発見（`--latest`/`--since`/`--dry-run`）で候補を確認し、確定したパスを明示指定して判定する。(3) **証拠はモデルが生成したプレーンテキストのみ** — `type=text` ブロックに限り、フェンス／4スペースインデント／HTMLコメント区間は除外する。除外した正規形マーカーは `marker_quoted` として記録し、黙殺しない。(4) **origin を判定に含める** — 「サブエージェント自身がマーカーを出した」と「親がサブの出力を自分のメッセージに転記した」を区別する。(5) **near-miss を捨てない** — 書式を外したマーカーは最も情報量の多い失敗なので `marker_malformed` として記録する
- **Rationale**: 判定手段が信用できないまま測定に進むことがこのプロジェクト最大の失敗パターン（D-5・D-7）。カバレッジ100%でも critical な偽 PASS が3回とも素通りしたため、カバレッジは品質指標として採用しない
- **Evidence**: 3巡のレビューで QA・Craft・Verification が独立に再現。最終ラウンドでレビュアーが別実装を書いて実データ33 transcript を再抽出した結果、イベント種別・origin・行番号・順序が完全一致（差分0）、origin は meta の `agentType` と1600/1600 一致、subagent transcript は44/44 が正しい位置に統合
- **Sources**: 本会話、コミット `a4d6fc0` → `47561ed` → `0afffc5`

# State

<!-- rn:state -->
- **Status**: paused
- **Date**: 2026-07-27
- **Last completed**: #1 の Step 2（`scripts/check_transcript.py` の作成)。コミット `a4d6fc0` → `47561ed` → `0afffc5`
- **Next**: #1 の「3ラウンド目の修正を検証し、レビューを1巡させる」— 12件中5件はスポット確認済み、残り7件が未検証(下記)
- **Notes**: ブランチ `feature/blackpink-setlist-planner`(push 済み、リモートと同期)。3巡目の修正(コミット `44e093d`)は tree にコミット済みだがレビュー未了。未検証の7件: (1) info付き連続フェンスの誤FAIL (2) 本物マーカーが `marker_quoted` で誤FAIL (3) `subagent_type` 欠落時の `to` フォールバック (4) `thinking` ブロックのテスト固定 (5) リスト項目内フェンスのテスト交絡 (6) `.coveragerc` の再現手順 (7) フィクスチャ説明の訂正。このセッションで QA レビューエージェントを起動しようとしたがユーザーに拒否され中断 — 再開時は先にユーザーへ意図を確認してから専門家レビューを起動すること。レビュー反復は上限3回に到達済みなので、残件が critical なら実測前にユーザーへエスカレーションする。測定はユーザー操作のコールドセッションでのみ行う(このセッション内で `/techtest` を起動しない)。判定コマンドは2段階(`--dry-run` で候補確認 → パス明示で判定)。
<!-- rn:state-end -->
