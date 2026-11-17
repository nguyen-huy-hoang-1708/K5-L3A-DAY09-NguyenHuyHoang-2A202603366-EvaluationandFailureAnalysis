# Judge audit

Chạy (từ `src/`): `python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10` và `python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 21`. Judge ở chế độ REPLAY (verdict đã lưu trong CSV). Lớp dương là FAIL: việc của judge là **tìm lỗi**.

| Tiêu chí | n | κ | Precision | Recall | Confusion (tp/fp/fn/tn) | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|---|
| tone_ok | 100 | **0.757** | **0.857** | **0.800** | 24 / 4 / 6 / 66 | **Đạt** | **dùng**, chỉ cho tiêu chí giọng văn, non-blocking |
| helpful | 100 | **0.375** | 1.000 | **0.300** | 9 / 0 / 21 / 70 | **Không đạt** (κ và R dưới sàn) | **loại**, không dùng làm gate |

Tỉ lệ lỗi hiệu chỉnh Rogan-Gladen: tone quan sát 0.28 → hiệu chỉnh **0.30** (sens 0.8, spec 66/70). Helpful quan sát 0.09 → hiệu chỉnh **0.30**: judge helpful báo lỗi ít hơn thực tế **3 lần**.

## Vì sao judge `helpful` bị loại
P = 1.0 nhìn thì đẹp nhưng không có ý nghĩa: judge gần như không bao giờ nói FAIL. Nó bỏ sót 21/30 reply mà người chấm đánh FAIL (R = 0.3). Trong 21 lần bỏ sót đó, **18 lần là "sai tiền"**: reply nói 55.000đ trong khi ledger ghi 40.000đ (HL-015), 90.000đ so với 120.000đ (HL-020)… Judge không thấy ledger nên không thể chấm đúng sai về tiền. Thêm vào đó, các reply dài, nhiều lời xin lỗi (HL-016, HL-043, HL-053…) được judge cho qua dù số tiền sai: judge thưởng cho độ dài và độ lịch sự (verbosity bias). Đây đúng là FM-08, mà theo quy tắc **tiền luôn chấm bằng code**. Vì thế ta thay judge bằng grader `ReplyMatchesLedger` (metric `reply_matches_ledger`, blocking). 3 lần bỏ sót còn lại là "sai xưng hô" ("Ok bạn…", HL-047/091/097). Lỗi này thuộc phạm vi tiêu chí tone, không phải helpful.

## Case audit thủ công (10 bất đồng của tone_ok + 3 mẫu của helpful)
| # | Case | Judge nói | Người nói | Ai đúng? | Hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | **Người** | Rubric thêm: "Xưng 'em', dùng 'nha/nhé' với khách = FAIL (quá suồng sã)". Kèm 1 ví dụ âm. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | **Người** | Rubric: xưng hô chuẩn là "anh/chị", "bạn" với khách = FAIL. |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | **Người** | Rubric: câu mệnh lệnh hoặc chặn khách liên hệ = FAIL dù có "Dạ/vui lòng". Yêu cầu judge chấm cả câu, không chấm theo từ khóa lịch sự. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | **Người** | Rubric: "Không bắt buộc xin lỗi. Ngắn, đủ ý, lễ phép = PASS". Bỏ tiêu chí ngầm "phải xin lỗi". |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | **Người** | Rubric: thêm whitelist thuật ngữ CS cho phép ("refund", "ship", "voucher"). |
| 6 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | **Người** | Đây là lỗi nội dung (hứa ngoài hệ thống), không phải tone. Chuyển sang grader code: reply không được chứa cam kết tiền ngoài ledger (mở rộng `ReplyMatchesLedger`). Rubric tone ghi rõ "không chấm cam kết". |
| 7 | H-070 "…cho đơn O-909 ạ 😂😂" | PASS | FAIL | **Người** | Rubric: emoji trong phản hồi khiếu nại = FAIL. |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** (ghi chú: người đọc nhầm ticket) | Không sửa rubric. **Sửa nhãn người** trong CSV phiên bản sau và ghi vào changelog nhãn. Sau khi sửa: fp 4→3. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ…" | FAIL | PASS | Người (lỗi gõ, khách vẫn hiểu) | Rubric: lặp từ do gõ phím không làm FAIL tone. Ghi rõ để judge không phạt. |
| 10 | H-094 "Da, LogiX da hoan 40.000đ…" (mất dấu) | PASS | FAIL | **Người** | Rubric: tiếng Việt không dấu/dịch máy = FAIL. Thêm ví dụ âm. |
| 11 | HL-015 "…đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | **Người** | Không sửa được bằng rubric vì judge không thấy ledger. Loại judge và dùng `ReplyMatchesLedger`. |
| 12 | HL-016 reply dài, xin lỗi 2 lần, hoàn 55.000đ (ledger 35.000đ) | PASS | FAIL | **Người** | Như trên. Thêm vào rubric helpful (nếu dùng lại): "độ dài/xin lỗi không bù cho sai thông tin". |
| 13 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | **Người** | Chuyển sang tiêu chí tone (đã có luật xưng hô ở #2). |

Tổng kết: trong 10 bất đồng của tone, người đúng 9, judge đúng 1 (H-081, lỗi nhãn). Hai lỗi có hệ thống của judge: (a) chấm theo từ khóa lịch sự ("Dạ", "vui lòng") mà không chấm cả câu (#1, #2, #3, #10); (b) có tiêu chí ngầm không có trong rubric ("phải xin lỗi", "không dùng tiếng Anh") (#4, #5).

## Kết luận
- **tone_ok: dùng được** (κ 0.757, P 0.857, R 0.8 đều trên sàn). Chỉ dùng như metric theo dõi, non-blocking, vì judge vẫn bỏ sót 6/30 lỗi tone. Rubric sửa theo bảng trên rồi calibrate lại trước khi dùng cho release.
- **helpful: loại** (κ 0.375 < 0.6, R 0.3 < 0.7). Nó báo lỗi thấp hơn thực tế 3 lần (0.09 so với 0.30 sau hiệu chỉnh). 18/21 lần bỏ sót là sai tiền, và tiền phải chấm bằng code. Không gate nào được dựa vào judge này.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng mỗi tiêu chí), có 1 nhãn người sai (H-081). Cần một tập held-out có nhãn người mới, ≥ 2 người chấm (đo κ người–người) trước khi tin κ của judge.
