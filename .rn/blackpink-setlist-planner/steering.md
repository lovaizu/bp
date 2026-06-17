# Goal

CC（Claude Code）とGHC（GitHub Copilot）の両方で多層サブエージェント構成を動かす「方法」を見つけ、再利用可能な形に確立する。

CCで設計した4層エージェント構成をマッピングルールでGHCに機械変換し、両プラットフォームでサブエージェントが動くことを実証する。成果物は ①動作する両プラットフォーム実装 ②マッピングルール／変換スクリプト ③方法とプラットフォーム差（サブエージェントを動かす勘所）の文書化。

BLACKPINKセットリスト・プランナーは検証用サンプル。プランナー自体も「同等の出力」も目的ではなく、「方法が両プラットフォームで動くか」を確かめる手段。

# Acceptance criteria

- CC・GHC 両方で `/bp <theme>` 呼び出しが layer-2 不変条件（ルーティング・Step順・filter実行・OUT形・委譲有無）を `scripts/verify-run.py` で PASS する
- 段階的ビルドアップの各段（A→B）で再現性が成立する（前段の PASS を後段で壊さない）
- プロンプトパターン3種（①サブエージェント呼び出し ②サブエージェントへの IN/OUT ③サブエージェントからのスクリプト呼び出し）が両プラットフォームで動くことを事実で示せる
- CC↔GHC の差分が「実装の工夫で解決したもの」と「プラットフォーム固有の制約として残るもの」に分類・文書化されている
- 設計書（`docs/cross-platform-agent-design.md`）が検証結果・マッピングルール・制約を反映した最終版になっている

# Assumptions

- GHC のデフォルトモデルが CC と比較して著しく性能差がある場合、CC/GHC 差はプラットフォーム差でなくモデル差になるため同等モデル指定が必要（未確認: GHC で Opus 4.x が選べるか）
- GHC transcript は最終OUT生成ターンを記録しない（事実: 実ログ6本で確認済み）→ 画面出力も証拠として使う
- `scripts/verify-run.py` は CC/GHC 両方の transcript 解析に対応済み（事実: A-2 step2 完了）
- stage-A（サブエージェント無し）では filter-songs.sh の実行方式が CC と GHC で異なる可能性がある（GHC は jq クォートで壊れた前例あり）
- `bp.prompt.md` の `tools: agent` は stage-A では未使用だが実証済み要件のため温存

# Rules

- 1 task = 1 commit
- 推測せず事実ベースで判断する（不確かな点は「未確認」と明記する）
- 測定は必ずユーザー操作のコールドセッションで行う（このセッション内で `/bp` を起動して測定してはいけない）
- filter-songs.sh の証拠ログ `/tmp/bp-filter.log` はリセットしない（貯めっぱなし運用・時間窓で切り分け）
- 1変数ずつ変えて再測定する（ビッグバン検証は行わない）
- GHC transcript は最終OUT生成ターンを欠く → 画面出力も証拠として保存する

# Tasks

### #1: GHC A-2 step3 — 同等モデルで6ラン測定

**Purpose**: GHC 初回6本はモデル交絡で無効（CC=Opus 4.8 vs GHC=GPT mini/Haikuレベル）。同等モデルを揃えて `/bp <theme>` を6ラン測定し、GHC stage-A の layer-2 不変条件を事実で確認する。

**Prerequisites**: none

**Steps**:

- [x] モデル方針を決定（D-4）: CC=Opus 4.8 流用・GHC=Sonnet 4.6 で再測定
- [x] GHC white × 2ラン完了（5d15c2b9, 984f0d7c）— checker 4/5 PASS, S2/S3 obs.limit, 画面確認 PASS
- [x] GHC quick-party × 2ラン（a90a8ee6, 49e33249）— C1-C3/C5 PASS, C4 obs.limit 画面確認 PASS
- [x] GHC versus × 2ラン（12d09754 PASS, 88dfcb11 C3 FAIL）— C4 obs.limit 画面確認 PASS
- [x] GHC 各ランを `python3 scripts/verify-run.py --platform ghc --theme '<theme>' <t.jsonl>` で判定する
- [x] 結果（CC 既存6本 + GHC 新規6本）を checks/task-1.md に記録する
- [x] self-check (OK — checks/task-1.md 参照)
- [ ] user review

**Completion criteria**:

- GHC で 6ラン全て `verify-run.py` 実行済み（PASS/FAIL を問わず各ランの判定結果が記録されている）
- 使用モデルが GHC=Sonnet 4.6・CC=Opus 4.8 であると明示されている
- FAIL が出た場合、FAIL した不変条件と transcript 上の根拠が記録されている

---

### #2: A-3 — CC/GHC 比較と差分文書化

**Purpose**: CC・GHC 両方で stage-A の layer-2 不変条件が全PASS することを確認し（または FAIL なら原因を1変数で特定し）、差分を文書化する。

**Prerequisites**: #1

**Steps**:

- [ ] CC 6/6 PASS・GHC 6/6 PASS の場合: 差分を「実装工夫で解決済み」と「プラットフォーム固有制約」に分類して記録する
- [ ] GHC FAIL がある場合: FAIL 不変条件を特定し、変換ルールまたは WF 文言を1変数だけ修正して再変換・再測定する
- [ ] 差分分類を `docs/cross-platform-agent-design.md` の該当セクションに反映してコミットする
- [ ] self-check (OK/NG per completion criterion, record in checks/task-2.md)
- [ ] user review

**Completion criteria**:

- CC・GHC の stage-A 結果が記録されている（PASS 数・FAIL 条件）
- FAIL があれば根本原因が1変数に特定されている（複数要因混在の場合は要因分離が先行）
- 差分が「解決済み」と「残存制約」に分類されて設計書に記録されている

---

### #3: B-1 — finder のみサブエージェント化（CC → GHC）

**Purpose**: `bp-song-finder` を1つ目のサブエージェントとして追加し、CC で layer-2 PASS を確認後、GHC に自動変換して両方で PASS することを示す。プロンプトパターン①（サブエージェント呼び出し）と②（IN/OUT）を実証する。

**Prerequisites**: #2

**Steps**:

- [ ] CC 側に `bp-song-finder` サブエージェント定義を基準1（IN/OUT・作業指示のみ）で作成する
- [ ] optimized WF の Step1 を finder 委譲形式に更新する
- [ ] CC で `/bp` 2ラン測定し `verify-run.py` 委譲あり版で PASS を確認する
- [ ] `convert-cc-to-ghc.py` で GHC 側を再生成する
- [ ] GHC で `/bp` 2ラン測定する（同等モデル・コールドセッション）
- [ ] CC/GHC の差分を記録する
- [ ] self-check (OK/NG per completion criterion, record in checks/task-3.md)
- [ ] user review

**Completion criteria**:

- CC で finder 委譲あり 2ラン以上 PASS
- GHC で finder 委譲あり 2ラン以上 PASS（または FAIL 原因が1変数で特定済み）
- サブエージェント呼び出し（①）と IN/OUT（②）のパターンが両プラットフォームで実証済みと記録されている

# Decisions

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

## D-3: 旧サブエージェント定義の削除（2026-06-02）
- **Issue**: A版（サブエージェント無し）の測定中に旧 big-bang サブエージェント定義3つが残存していた
- **Conclusion**: `.claude/agents/bp-*.md`（3）と `.github/agents/bp-*.agent.md`（3）を git rm し、A版クリーン環境に
- **Rationale**: A版の不変条件5（委譲なし）を正しく測定するために旧定義は除去が必要。B段階の流用元にもなれない（基準1違反：ペルソナ・判断動詞を含む）
- **Evidence**: A-1測定でA版はTask0（委譲なし）が確認済み
- **Sources**: commit 6b1d4f2

## D-4: GHC を Sonnet 4.6 で測定、CC Opus 4.8 結果は流用（2026-06-17）
- **Issue**: GHC 初回6本は GPT mini/Haiku レベル（小型モデル）で実行されていた可能性が高く、指示追従不足がプラットフォーム差と分離不能だった
- **Conclusion**: GHC を Sonnet 4.6 で再測定する。CC は Opus 4.8 のまま（既存6/6 PASS を流用）
- **Rationale**: 目的はプラットフォーム差の検証であり、厳密なモデル一致ではない。Sonnet 4.6 以上であれば指示追従の性能差は許容範囲。CC Opus 4.8 の再測定は不要
- **Evidence**: ユーザー確認「Sonnet 以上であれば CC と GHC で同等。比較したい訳ではない」（2026-06-17）
- **Sources**: 本会話

# State

<!-- rn:state -->
<!-- replace this comment block with live state when pausing -->
<!-- template: Status / Date / Last completed / Next / Notes -->
