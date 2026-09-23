1. Ligar o USB e ver se a porta aparece
pio device list

2. Preencher o secrets.h

3. Iniciar o servidor e liberar a porta 8000
cd server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

Uma vez só, num PowerShell como administrador:
New-NetFirewallRule -DisplayName "TCC 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow

(para simular na webcam):
python simulator/virtual_device.py --source 0 --preview
server\.venv\Scripts\python simulator\virtual_device.py --source 0 --preview

4. Gravar e abrir o monitor serial
cd firmware\esp32cam
pio run -e esp32cam -t upload
pio device monitor

se quiser testar somente sonar:
pio run -e bench_sonar -t upload