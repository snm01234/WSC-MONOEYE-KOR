# tools/archive

이 폴더의 스크립트는 **현재 메인 tip 파이프라인에서 쓰이지 않는다.** 지우지 않고 옮겨만
둔 것이다. 대부분 다음 중 하나다.

* 이미 적용이 끝난 1회성 수리 (`fix_*`, `repair_*`, `restore_*`)
* 폐기된 실험·PoC (`poc_*`, `run_*_ab`, `*_hyp*`)
* 초기 파이프라인의 중간 단계 (지금은 다른 도구가 대체)

되돌리려면 파일을 `tools/`로 복사해 오면 된다. 어디서 왔는지는
`out/patch/archive_unused_tools.json` 매니페스트에 있다.

판정 근거는 `tools/audit_tool_usage.py`가 만든 `out/patch/tool_audit.json`이다.
남아 있는 도구가 import 하는 모듈은 이 폴더로 옮기지 않는다 — 옮기기 전에 import 폐쇄
검사를 하고, 하나라도 걸리면 그 파일은 제외한다.

**주의:** 여기 있는 ROM 쓰기 스크립트를 그냥 실행하지 말 것. 대부분 당시의 중간 ROM 상태를
전제하고, 지금 tip에 돌리면 이미 고친 결함을 되살릴 수 있다.
