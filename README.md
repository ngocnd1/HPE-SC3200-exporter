# HPE Scale-up 3200 Redfish Exporter

[![CI](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml)

Python Prometheus exporter chuyên cho HPE Scale-up 3200 RMC. Client ưu tiên
Redfish `$expand=.` để lấy collection members trong một request, đồng thời tự
fallback về GET tuần tự nếu firmware không expand response.

## Metric contract v0.3

- `redfish_up`, `redfish_scrape_duration_seconds`
- `redfish_resource_health`, `redfish_resource_power_state`
- `redfish_system_info` (manufacturer, model, serial, BIOS, system type)
- `redfish_system_processor_count`, `redfish_system_logical_processor_count`, `redfish_system_memory_bytes`
- `redfish_temperature_celsius`, `redfish_fan_speed_rpm`, `redfish_chassis_power_input_watts`
- `redfish_sensor_ratio`, `redfish_sensor_power_watts`, `redfish_sensor_voltage_volts`
- `redfish_power_supply_input_watts`, `redfish_power_supply_output_watts`

Xem danh sách label, mapping và PromQL ổn định tại [docs/METRICS.md](docs/METRICS.md).

Triển khai systemd, vmagent và Grafana/VictoriaMetrics xem
[deployment/README.md](deployment/README.md). Năm dashboard JSON nằm trong
`grafana/dashboards/` và tự mở rộng theo label `target` khi thêm RMC mới.

Không đưa IP, URI, sensor ID biến động hoặc firmware payload vào label tùy tiện: cardinality sẽ bùng nổ khi giám sát nhiều máy.

## Cài đặt trên Linux

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin redfish-exporter
sudo mkdir -p /opt/redfish-exporter /etc/redfish-exporter
sudo cp -a . /opt/redfish-exporter/source
sudo python3 -m venv /opt/redfish-exporter/venv
sudo /opt/redfish-exporter/venv/bin/pip install /opt/redfish-exporter/source
sudo cp config.example.yml /etc/redfish-exporter/config.yml
sudo cp deploy/redfish-exporter.service /etc/systemd/system/
sudo chmod 640 /etc/redfish-exporter/config.yml
sudo chown root:redfish-exporter /etc/redfish-exporter/config.yml
```

Tạo `/etc/redfish-exporter/secrets.env`:

```ini
REDFISH_SC3200_01_PASSWORD=replace-me
```

```bash
sudo chmod 600 /etc/redfish-exporter/secrets.env
sudo systemctl daemon-reload
sudo systemctl enable --now redfish-exporter
curl -s http://127.0.0.1:9610/metrics | grep '^redfish_'
```

## Kiểm thử trước khi đưa vào production

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/pytest
```

Smoke test bằng server mockup chính thức của DMTF (không cần Docker):

```bash
chmod +x scripts/test-with-dmtf-mockup.sh
./scripts/test-with-dmtf-mockup.sh
```

Lệnh trên clone `DMTF/Redfish-Mockup-Server` để smoke test client. Để kiểm
thử mapping SC3200, truyền thư mục mockup đã capture và khử bí mật:

```bash
./scripts/test-with-dmtf-mockup.sh /path/to/sc3200-mockup
```

Xem [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) trước khi thêm collector hoặc OEM adapter.

Khi có quyền truy cập HPE RMC thật, đọc [docs/RMC_PAYLOAD.md](docs/RMC_PAYLOAD.md) và chạy `tools/capture_redfish_payload.py`. Script chỉ GET, tự bỏ qua account/log/network configuration và che các trường nhận dạng phổ biến.

Đối với certificate nội bộ, dùng `ca_file`; `verify_tls: false` chỉ phù hợp thử nghiệm. Account Redfish chỉ cần quyền đọc. Chu kỳ scrape nên bắt đầu ở 60 giây vì RMC/iLO không phải time-series database và có thể phản hồi chậm.

## Mở rộng đúng cách

1. Collector chuẩn vẫn là nền tảng; thêm metric từ chuẩn mới như `ThermalSubsystem`, `PowerSubsystem`, `EnvironmentMetrics` khi firmware hỗ trợ.
2. Nếu HPE chỉ trả dữ liệu qua `Oem.Hpe`, tạo module adapter riêng và fallback sau dữ liệu chuẩn; không trộn logic OEM vào discovery.
3. Chụp mockup từ từng firmware/model thật, loại bỏ bí mật, rồi viết fixture test. Tên model không đủ để đảm bảo hai phiên bản firmware trả cùng schema.
4. Thêm cache topology nếu thiết bị lớn; nhưng không cache reading cảm biến.

## Giới hạn bản khung

Đây là MVP đọc chuẩn `Thermal`/`Power` kiểu phổ biến. Superdome Flex hoặc Scale-up 3200 có thể công bố sensor qua schema mới hay `Oem.Hpe`; cần một mẫu JSON/mockup thực tế để ánh xạ chính xác. Không có payload thực thì mọi tuyên bố “hỗ trợ đầy đủ” chỉ là đoán.
