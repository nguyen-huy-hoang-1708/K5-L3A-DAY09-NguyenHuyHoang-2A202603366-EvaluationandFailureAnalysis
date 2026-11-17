# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

Output (rút gọn):
```
== seeded_regression C-014@11  lab8 grader: FAIL (proposal_mismatch)
   1 handoff            {... "claim_id": "C-014", "task": "check_applicable_policy" ...}
   2 investigator       {"lookup_order": {"visible": true, "order": {"order_id": "O-515", "status": "delivered_late", ...
   3 quote              {"amount_vnd": 150000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03", "DMG-04"], ...}
   4 refund_history     {"refund_count": 1, "refunded_vnd": 30000}
   5 proposal           {"amount_vnd": 120000}
   6 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
-- ledger vs label
  refund_vnd     ledger=90000    label=120000   <-- differs
== fixed C-014@11  lab8 grader: PASS (ok)
   3 quote              {"amount_vnd": 150000, ...}
   4 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
  refund_vnd     ledger=120000   label=120000
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `E-seeded_regression-20261117-a8af` (item E-014, cả 5 seed đều fail) |
| Triệu chứng | `pass_rate` FAIL (`proposal_mismatch`), `refund_amount_correct` FAIL (ledger 90.000 ≠ nhãn 120.000), `reply_matches_ledger` FAIL. Reply nói **120.000đ**, trùng nhãn nhưng ledger chỉ ghi **90.000đ**. Ai chỉ đọc reply sẽ tưởng trial này đúng. |
| Bước sai đầu tiên | **Event 5 `proposal {"amount_vnd": 120000}`**: agent tự lấy 150.000 trừ 30.000 đã hoàn rồi mới đưa cho host. |
| Vì sao là bước *đầu tiên* | Event 2 đọc đúng đơn O-515 (trễ 5 ngày, hỏng hàng đã verify). Event 3 `quote` 150.000 = max(SHIP-LATE-03 30.000, DMG-04 150.000), đúng luật "lấy số lớn hơn". Event 4 `refund_history` chỉ là lệnh đọc, trả đúng 30.000 đã hoàn trước đó. Sai bắt đầu khi agent **dùng** số đó để tự trừ: luật Lab 8 (`docs/lab8/business_rules.md`) bắt truyền quote đầy đủ cho `issue_refund`, host tự trừ phần đã hoàn. Host trừ thêm 30.000 nên ledger còn 90.000. |
| Failure mode | **FM-05 pre_deducted**. Event 6 còn có thêm FM-08 phát sinh: reply báo số agent tự tính chứ không phải số host ghi. |
| Đối chứng | `fixed C-014@11`: không có event `refund_history`/`proposal`. Quote 150.000 đi thẳng vào host, host cap theo đơn và ghi 120.000 = nhãn, reply 120.000đ = ledger. |
| Regression test | `src/tests/test_lab9_trace_case.py` (3 test, marker `student`): (1) bước sai đầu tiên là `proposal` 120000 đứng sau `handoff, investigator, quote, refund_history`; (2) trên `seeded_regression`, `RefundAmountCorrect` và `ReplyMatchesLedger` đều trả False; (3) trên `fixed` cả hai trả True và grader Lab 8 PASS. Đã chạy: 3 passed. Trong eval set, item E-014 có `provenance.from_trace` trỏ về trial này. |
| Chưa chắc chắn | Trace không cho biết **vì sao** agent gọi `lookup_refund_history` (MockLLM không có lý do). Với LLM thật, bước này có thể do prompt gợi ý "kiểm lịch sử hoàn tiền". Trace cũng không chứng minh lỗi chỉ xảy ra khi đơn đã từng được hoàn: ở C-008 (`refunded_vnd` 0) agent vẫn tự tính và reply sai (FM-08), dù ledger đúng. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
