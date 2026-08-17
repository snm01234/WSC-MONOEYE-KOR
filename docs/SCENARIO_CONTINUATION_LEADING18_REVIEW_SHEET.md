# Scenario continuation 선두 `18 → こ` 구조 검수 시트

메인 TIP: `CFB90AAA7AF2B9336FB63C70A8E7EC760AC51425D80017D5DAF82E6118D86BCA`  
전체 residual candidate: **2,847건**  
현재 `18 + E5 18` direct 구조: **2,740건**  
직후 `08/17` control 인접: **703건**  
과거 번역 시트와 비교해 선두 `こ` 하나만 다른 행: **696건**  
원본 payload/시트 body/일본어/동일 bundle predecessor까지 일치해 `18=구조 prefix`가 증명되는 행: **2,847건**  
원문 결합은 20셀 이하이나 번역 결합이 20셀 초과인 reflow 증거: **384건**  
원문/번역 결합 모두 20셀 초과라 원본부터 split이 필요한 행: **2,249건**

## 왜 과거 수정이 전체 반영되지 않았는가

- 추출 시트는 이미 `prefix_hex=18`과 본문을 분리했지만, runtime contract는 caller trace가 없는 continuation을 `quarantine`으로 둔다.
- 따라서 `18`을 전역적으로 제거하지 않았고, 사용자가 실제 화면 오류를 확인한 주소만 좁게 복구했다.
- `6002F1`은 과거 실측 후 선두 18을 제거해 현재 static text에서도 `こ`가 사라진 반면, `60BB48`은 여전히 `18E518...`라 이번 화면에서 실제 `こ`가 노출됐다.
- 최근 220/59건 복구는 `scenario_first` exact4/제어 인접 문제를 대상으로 했으므로 이 `scenario_continuation` quarantine 집단은 범위 밖이었다.

## 우선순위

| Tier | 건수 | 의미 | 권장 처리 |
|---|---:|---|---|
| `P0_runtime_proven_bad` | 0 | 현재 사용자 실측 오류 | 즉시 좁은 후보 수정/실측 |
| `P1_direct_ext3_exact_text_control_adjacent` | 216 | 선두 こ만 불일치 + direct ext3 + 즉시 08/17 | P0 PASS 후 1차 일괄 후보 |
| `P2_direct_ext3_exact_text` | 425 | 선두 こ만 불일치 + direct ext3 | 2차 bundle 단위 후보 |
| `P3_direct_ext3_control_adjacent_text_drift` | 427 | direct ext3 + 즉시 제어, 번역문은 이후 변경 이력 있음 | 현행 번역 확인 후 구조만 수정 |
| `P4_direct_ext3_text_drift` | 1,672 | direct ext3, 번역문 변경 이력 있음 | 문맥 검토 후 단계 처리 |
| `P5_non_direct_native_or_other` | 107 | native/기타 경로; 정적 false positive 가능 | 자동 수정 금지 |

## P0 / P1 상위 검수 대상

| Tier | 주소 | 현재 static | `18` 제어 시 기대 | 직후 control | 이전 문맥 | 다음 대사 |
|---|---|---|---|---|---|---|
| `P1_direct_ext3_exact_text_control_adjacent` | `60022E` | 그럼、　해리　중위의　동행을　허가한다。 | 그럼、　해리　중위의　동행을　허가한다。 | `085B` | ……어쩔　수　없군。 | ……알겠다！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `600455` | 이、　이런　짓、　깅가남에게는……！ | 이、　이런　짓、　깅가남에게는……！ | `1728` | 오오……！！ | 이런　일은　명령하지　않았다！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60074C` | 여기에　브라이트　중령　일행이　있다！！ | 여기에　브라이트　중령　일행이　있다！！ | `1728` | 이　건물까지　전진시켜라！ | ……로랑　군！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60081B` | ……자、　와라！ | ……자、　와라！ | `1728` | 얼마든지　설명해　주지。 | 앗！　기、　기다리게나…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `60085F` | ……자、　디아나　님、　이쪽으로。 | ……자、　디아나　님、　이쪽으로。 | `0845` | 지금은　여기서　벗어나는　게　급선무다。 | ……브라이트　함장。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `600A72` | 그쪽과　합류하겠습니다。 | 그쪽과　합류하겠습니다。 | `1728` | 지금은　적을　물리치는　게　먼저입니다。 | 그럼、　브라이트　중령。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `600AE1` | 그쪽은　괜찮으십니까！？ | 그쪽은　괜찮으십니까！？ | `1728` | 디아나　여왕！ | ……네。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `600B96` | ……본때를　보여주마！ | ……본때를　보여주마！ | `1728` | 디아나　님을　업신여기다니！ | ……좋아！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `600D74` | ……외롭지는　않을　거야。 | ……외롭지는　않을　거야。 | `1728` | 네　곁으로　갈　거다…… | ……그럼、　작별이다。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `600EE3` | 그、　그럴　수가……！！ | 그、　그럴　수가……！！ | `1728` | 필、　필　소령님！？ | 아앗！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `601179` | 이렇게　된　이상……！！ | 이렇게　된　이상……！！ | `1728` | 하하하하핫！！ | 저건！？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6012A5` | 어리석군……！ | 어리석군……！ | `1728` | ……구엔　라인포드。 | ……아인！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6012D9` | 적당히　좀　해、　거추장스럽다고！！ | 적당히　좀　해、　거추장스럽다고！！ | `1728` | 또　너냐！！ | ……사라져　버려！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `601347` | 너　같은　놈한테　질까　보냐！！ | 너　같은　놈한테　질까　보냐！！ | `1728` | 파워업했어！！ | ……아니。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60136B` | 이제　두　번　다시…… | 이제　두　번　다시…… | `1728` | ……아니。 | 나는！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6016E1` | 그렇지　않으면　자네가　죽어！！ | 그렇지　않으면　자네가　죽어！！ | `1728` | 그녀는　예전의　그녀가　아닐세！！ | 그건　무리라고요！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60173B` | 쏠　수　있을　리가　없잖아요！！ | 쏠　수　있을　리가　없잖아요！！ | `082B` | 그녀는　그　세라　씨라고요！ | 그렇지　않으면　너희가　죽는다。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `6018E6` | 결코　다른　뜻은　없습니다！ | 결코　다른　뜻은　없습니다！ | `0845` | 생각을　바꾸시게　하려던　것뿐입니다。 | ……필이여。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60239B` | 그　세라　씨가　마치　로봇처럼…… | 그　세라　씨가　마치　로봇처럼…… | `0802` | 나도　충격이었으니까。 | ……그만해！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6023F2` | 말이　너무　심했……네。 | 말이　너무　심했……네。 | `0802` | ……미안해。 | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `6024FC` | 라라아　가　죽었던　그날부터　말이지。 | 라라아　가　죽었던　그날부터　말이지。 | `082B` | 지금도……　그리고　예전에도。 | ………윽！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6026C7` | 지금은　전투에　전념해　다오！ | 지금은　전투에　전념해　다오！ | `0883` | 그　모빌슈트는　나중에　봐도　된다！！ | 네、　옛！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602788` | 로랑은　전투를　계속해　다오！ | 로랑은　전투를　계속해　다오！ | `0883` | 누군가에게　확인해　보자。 | 네、　옛！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602988` | 자신의　무력함을　깨닫게　해　주마！ | 자신의　무력함을　깨닫게　해　주마！ | `1728` | 어디　덤벼　보아라！ | 브라이트　함장님……！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6029E1` | 작전을　개시한다！！ | 작전을　개시한다！！ | `084B` | 여기서　물러설　수는　없다！ | ……알겠습니다！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602ACE` | ……부탁한다！！ | ……부탁한다！！ | `1728` | 너희라면　반드시　해낼　수　있다！！ | 콜로니　레이저　조사　준비를！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602DD1` | 이놈……！！ | 이놈……！！ | `1728` | 네、　네놈　같은　애송이　따위가……！ | 해냈다……！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602E75` | 월광접이다！！ | 월광접이다！！ | `1728` | 크크크크……！ | 뭐야！？　……으악！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `602FD3` | 살려둘　수는　없다！！ | 살려둘　수는　없다！！ | `0857` | 너는　전투　의지를　낳는　근원이다！！ | ……윽！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60326F` | 방、　방금　그것은…… | 방、　방금　그것은…… | `1728` | 으윽…… | ……내　꿈？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `603307` | 우리들은……！！ | 우리들은……！！ | `1728` | 기다려、　하만　칸！！ | 그만둬、　하만！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60340A` | 놓쳐버렸는가…… | 놓쳐버렸는가…… | `1728` | …………… | 에너지　반응　감소！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6034BB` | 하만　님은　어디에　계시는가！？ | 하만　님은　어디에　계시는가！？ | `1728` | 콜로니　레이저가！！ | ……안　됩니다！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `603582` | 콜로니　레이저를　제압한다！！ | 콜로니　레이저를　제압한다！！ | `084B` | 콜로니　레이저에　접현시켜라！！ | ……알겠습니다！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6035D9` | 모두、　아주　잘해　주었다！！ | 모두、　아주　잘해　주었다！！ | `1728` | 작전　성공인가！！ | ……뭐지！？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `603A7B` | ……반드시　녀석은　개입해　올　것이다。 | ……반드시　녀석은　개입해　올　것이다。 | `0809` | 최후의　결전이　될　거야…… | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `604A99` | 해치를　열어다오。　나가겠다！！ | 해치를　열어다오。　나가겠다！！ | `0891` | ……그렇다면　문제없겠군。 | 아、　이봐！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `604AEE` | ……이봐、　거기　돔！！ | ……이봐、　거기　돔！！ | `1728` | ……………… | ……윽！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `605035` | 그럼、　전투　개시다。 | 그럼、　전투　개시다。 | `1728` | ……좋아。 | 전투에는　또　하나 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60508C` | 예를　들어、　내　경우에는　이것이다。 | 예를　들어、　내　경우에는　이것이다。 | `1728` | 전투　능력을　올릴　수　있다。 | ……격추한다！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `605183` | 『스택』을　구성해서　싸운다！ | 『스택』을　구성해서　싸운다！ | `1728` | 한　기씩　싸우면　불리하겠군…… | 스택？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `605231` | 그렇다면、　어떻게　구성하면　되지？ | 그렇다면、　어떻게　구성하면　되지？ | `1728` | 짜자는　거군……　알겠다！ | 유닛을　같은　칸에　옮기면　된다。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `605B29` | 나와　중위가　직접　마중　나가겠다。 | 나와　중위가　직접　마중　나가겠다。 | `0891` | 그럼　접현　허가를　내줘。 | ……예。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `605C62` | 말해두지만、　부대　내　연애는…… | 말해두지만、　부대　내　연애는…… | `0801` | 반했나？ | 그러니까、　아무것도　아니라고……！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `605F49` | ……무슨　문제라도　있는　건가？ | ……무슨　문제라도　있는　건가？ | `0801` | 인사는　이미　끝냈다。 | 아니…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `605F75` | 그래서、　나한테　무슨　용건이지？ | 그래서、　나한테　무슨　용건이지？ | `0803` | ……괜찮아。 | 하나　묻고　싶은　게　있었어。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `606141` | 방해했군。 | 방해했군。 | `1728` | 그럼　브라드　중령에게라도　물어보지。 | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `6061ED` | 「목마」　추격에　참가하라는　내용이다。 | 「목마」　추격에　참가하라는　내용이다。 | `0801` | 그라나다의　명령서를　받았다。 | 목마라고요！？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `606BB5` | 그래서、　명령은　전했겠지？ | 그래서、　명령은　전했겠지？ | `0891` | ……그런가。 | ……예。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `606D7B` | 시그　중위！　준비는　되었겠지！？ | 시그　중위！　준비는　되었겠지！？ | `1728` | ……알겠다！ | 여기는　시그　중위。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `606F45` | ……두고　봐라！！ | ……두고　봐라！！ | `1728` | 저　정도　적은　밀어낼　수　있을　거야！ | ……해내　보이겠어！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6070C2` | ……미안하지만、　처리하겠다！ | ……미안하지만、　처리하겠다！ | `1728` | 이것도　임무의　일부라서　말이지…… | 대、　대단하군……！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `607206` | ……이상、　통신　종료다。 | ……이상、　통신　종료다。 | `0830` | 이대로　전투를　계속하겠다。 | ……아、　이봐！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60726A` | 저　하얀　녀석을　단숨에　몰아치겠다！ | 저　하얀　녀석을　단숨에　몰아치겠다！ | `1728` | 조금씩　싸워선　끝이　없다。 | ……이봐！？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6072B8` | 저　하얀　녀석을　단숨에　몰아치겠다！ | 저　하얀　녀석을　단숨에　몰아치겠다！ | `1728` | 조금씩　싸워선　끝이　없다。 | 뭐、　뭐라고！？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60760F` | 아、　아니……　지금은　대령이셨지。 | 아、　아니……　지금은　대령이셨지。 | `1728` | 오！　샤아　소령！！ | 후후……　여전하군、　드렌。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `6076C3` | 준비는　되었나？ | 준비는　되었나？ | `1728` | ……그리고　라라아。 | ……네、　대령。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60789C` | 전　기체、　목마를　놓치지　마라！ | 전　기체、　목마를　놓치지　마라！ | `1728` | 목마　녀석、　도망치려는　건가！！ | 아르테이시아…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `607BE6` | 저　파일럿…… | 저　파일럿…… | `1728` | ……………… | ……왜　그러지、　라라아？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `607D75` | ……전　기체、　후퇴하라！ | ……전　기체、　후퇴하라！ | `1728` | 포기할　수밖에　없나……！！ | 음、　도망친　건가……？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `608530` | 틀림없습니다。 | 틀림없습니다。 | `0854` | 모빌아머로　되어　있습니다。 | ……좋아。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `608553` | 파일럿은　사살하지　마라！ | 파일럿은　사살하지　마라！ | `1728` | 전　기、　저　모빌아머를　노려라！ | 세레인　소위、　아인　상사、 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60857D` | 상대　수가　너무　많다……　도망친다！ | 상대　수가　너무　많다……　도망친다！ | `1728` | 즉시　귀환해라！ | 이렇게　많은　적을　상대하고 |
| `P1_direct_ext3_exact_text_control_adjacent` | `60867E` | 누구　사람으로서　할　수　없잖아！ | 누구　사람으로서　할　수　없잖아！ | `1728` | 적　격파가　아니라　우리　생존이다！ | 좋아、　지금이다！……　쏴라！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `608730` | 안　돼……　피해야　해요、　소위님！ | 안　돼……　피해야　해요、　소위님！ | `1728` | 세레인　소위가……！ | ……설마　매복이라니。 |
| `P1_direct_ext3_exact_text_control_adjacent` | `6087B9` | ……공격해라！ | ……공격해라！ | `1728` | 세　기로　쓰러뜨리겠다고！？ | 역시　이렇게　나오는군…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `608AC5` | 자、　그렇게　생각하지　않나……！？ | 자、　그렇게　생각하지　않나……！？ | `082B` | 제법이로군、　지구의　야만인　녀석들도！ | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `608BA1` | 정신　차려라！！ | 정신　차려라！！ | `0803` | 세라！！ | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `608DEC` | 세레인……　아니、　세라를　구한다！！ | 세레인……　아니、　세라를　구한다！！ | `1728` | 그거야　뻔하지…… | 핫！　꽤나　애먹고 |
| `P1_direct_ext3_exact_text_control_adjacent` | `608E1E` | 그　바스크　옴도　참　꼴불견이로군！ | 그　바스크　옴도　참　꼴불견이로군！ | `1728` | 있는　모양이군…… | 말이　지나치군、　야잔　게이블！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `608E96` | 지시받지　않아도　처리해　주마！ | 지시받지　않아도　처리해　주마！ | `1728` | 기체를　확보하면　되는　거지？ | 음！　저　붉은　겔구그는　설마…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `608FC9` | 킴、　크라비츠！　준비는　되었나！！ | 킴、　크라비츠！　준비는　되었나！！ | `0890` | 인사는　살아난　뒤에　해。 | ……알겠다！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `609477` | ……그렇지만、　그게　아니야。 | ……그렇지만、　그게　아니야。 | `1728` | 『동요한　것처럼　보이지　않는다』고？ | ………？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60953C` | ……나는。 | ……나는。 | `0801` | ’플라나간의　뉴타입’인　거야 | 이제　됐어！　그만해！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `609709` | ……시…그。 | ……시…그。 | `1728` | ……………… | ……………… |
| `P1_direct_ext3_exact_text_control_adjacent` | `6097B8` | 방금　전　이야기는　진심입니까？ | 방금　전　이야기는　진심입니까？ | `0803` | 그보다、　세라。 | ……진심？ |
| `P1_direct_ext3_exact_text_control_adjacent` | `6098CB` | 그렇다면、　더는　대화할　가치가　없군。 | 그렇다면、　더는　대화할　가치가　없군。 | `0806` | 네　얘기는　그게　전부인가？ | 아、　세라……！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `609D7D` | 뒷일을……　부탁하네……！！ | 뒷일을……　부탁하네……！！ | `1728` | 미안하다、　가토。 | 이놈들……！！ |
| `P1_direct_ext3_exact_text_control_adjacent` | `60A0B4` | ……아、　그래。 | ……아、　그래。 | `1719` | …………… | 아직도　목마에서　내리지　않았다니…… |
| `P1_direct_ext3_exact_text_control_adjacent` | `60A6EC` | 지금부터　포격을　개시한다！ | 지금부터　포격을　개시한다！ | `1728` | 지정　지점에　도착했다！ | 몬시아、　베이트、　아델！ |

전체 2,849건은 CSV에서 필터링한다. `P5`는 이미 runtime-proven native-only 행이 섞일 수 있으므로 자동 수정하면 안 된다.

## 회귀 기준

- bad anchor: `60BB48` — 화면에서 `こ뜻입니까！？` 재현.
- historical fixed anchor: `6002F1` — 과거 선두 `18=こ` 실측 후 payload를 18 없이 rehome하여 정상화.
- runtime-native-safe anchors: `63449B`, `635855`, `635BFB`, `635866`, `635C0C` — static `18` 해석만 보고 일괄 삭제하면 안 되는 반례군.
