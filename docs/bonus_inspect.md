# Bonus: chuyển 3 grader sang Inspect AI và so khớp

Code: `src/lab9_bonus/inspect_port.py`. Phụ thuộc riêng: `src/requirements-bonus.txt` (`inspect_ai==0.3.290`). CI không cần file này.

```bash
cd src
pip install -r requirements.txt -r requirements-lab9.txt -r requirements-bonus.txt
python -m lab9_bonus.inspect_port                                    # v1: swarm, fixed, seeded_regression
python -m lab9_bonus.inspect_port --spec config/metric_spec_v2.yaml \
    --shapes swarm,fixed,honest_refactor,seeded_regression,slice_regression,flaky_regression,probe_sibling_refund,probe_misreport
```

## Cách chuyển
| Thành phần | pydantic-evals (`lab9.eval`) | Inspect AI (`lab9_bonus.inspect_port`) |
|---|---|---|
| Đơn vị | `Case` = item@seed | `Sample` (id `E-xxx@seed`, target = nhãn JSON) |
| Chạy agent | task `make_task(...)` | `@solver replay_shape`: gọi cùng `make_task`, cất Trial vào `store()`; không gọi model (`mockllm/model`, offline) |
| Grader | `Evaluator` trong `lab9.graders` | `@scorer`: `approval_bypass`, `refund_amount_correct`, `reply_matches_ledger` |

Ba scorer được **viết lại từ định nghĩa metric**, không import `lab9.graders`. Vì vậy hai bên khớp nhau là kiểm tra chéo thật giữa hai bản cài đặt độc lập, không phải chạy cùng một code hai lần. Bộ so sánh đối chiếu **từng row (item, seed)**, không chỉ so trung bình.

Có 2 shape thăm dò (`probe_sibling_refund`, `probe_misreport`), tạo bằng `make_shape` của starter (không sửa `lab9/variants.py`). Lý do: trên mọi variant công khai `approval_bypass` đều bằng 0, nếu không thêm probe thì grader này chưa bao giờ được thử ở nhánh "có lỗi".

## Kết quả (`src/reports/bonus_inspect_v1.txt`, `src/reports/bonus_inspect_v2.txt`)
| Eval set | Shape | approval_bypass (pe / inspect) | refund_amount_correct | reply_matches_ledger | Row khớp |
|---|---|---|---|---|---|
| v1 | swarm | 0 / 0 | 0.9857 / 0.9857 | 1.0 / 1.0 | 70/70 |
| v1 | fixed | 0 / 0 | 1.0 / 1.0 | 1.0 / 1.0 | 70/70 |
| v1 | seeded_regression | 0 / 0 | 0.8571 / 0.8571 | 0.7857 / 0.7857 | 70/70 |
| v2 | swarm | 0 / 0 | 0.9786 / 0.9786 | 1.0 / 1.0 | 140/140 |
| v2 | fixed | 0 / 0 | 1.0 / 1.0 | 1.0 / 1.0 | 140/140 |
| v2 | honest_refactor | 0 / 0 | 1.0 / 1.0 | 1.0 / 1.0 | 140/140 |
| v2 | seeded_regression | 0 / 0 | 0.7857 / 0.7857 | 0.7143 / 0.7143 | 140/140 |
| v2 | slice_regression | 0 / 0 | 0.9357 / 0.9357 | 1.0 / 1.0 | 140/140 |
| v2 | flaky_regression | 0 / 0 | 0.9643 / 0.9643 | 1.0 / 1.0 | 140/140 |
| v2 | probe_sibling_refund | **55 / 55** | 0.6071 / 0.6071 | 1.0 / 1.0 | 140/140 |
| v2 | probe_misreport | 0 / 0 | 1.0 / 1.0 | **0.7857 / 0.7857** | 140/140 |

Kết luận: **1.330/1.330 row khớp cho cả 3 metric**, gồm cả các row có lỗi (55 approval bypass, 30 reply sai tiền). Hai harness cho cùng quyết định trên cùng trace.

## Khác biệt và giới hạn quan sát được
- Inspect đọc sample trong log **lười** (lazy). Phải lấy điểm khi thư mục log còn tồn tại; bản đầu tiên crash `FileNotFoundError` vì đọc sau khi đã xóa temp dir.
- Inspect cho điểm dạng số (0/1) và gom bằng metric `mean()`. Với metric an toàn, ta đếm tổng thay vì lấy trung bình, giống `lab9.eval.aggregate`.
- Đây là kiểm tra chéo **grader**, không phải kiểm tra **gate**: paired bootstrap, slice, integrity vẫn chạy trên report của `lab9.eval`.
- Cả hai cùng dùng chung regex số tiền cho FM-08, nên một lỗi chung của định nghĩa (ví dụ số viết bằng chữ) sẽ không lộ ra qua so sánh này.
