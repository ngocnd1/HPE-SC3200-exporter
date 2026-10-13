# Contributing

Mỗi thay đổi collector cần: một payload/fixture đã khử bí mật, test cho trường thiếu hoặc `null`, và mô tả metric/label. Không thêm logic dựa trên tên model nếu có thể khám phá bằng link Redfish. OEM adapter phải là fallback, không được ghi đè dữ liệu chuẩn hợp lệ.

Chạy `make test`, sau đó chạy `scripts/test-with-dmtf-mockup.sh`. Với blade mockup đã giải nén từ DSP2043, truyền thư mục gốc mockup làm đối số cho script.
