@echo off
echo ============================================
echo  HoloMind - Instalando dependencias
echo ============================================
pip install opencv-python mediapipe numpy networkx
echo.
where node >nul 2>nul
if %errorlevel%==0 (
  echo Node.js detectado. Instalando backend...
  pushd backend
  if not exist .env copy .env.example .env >nul
  call npm install
  popd
  echo Backend pronto.
) else (
  echo Node.js nao encontrado. Backend WhatsApp nao foi instalado.
)
echo.
echo Dependencias instaladas.
echo.
echo Para executar UI: python main.py
echo Para executar backend: cd backend ^&^& npm start
pause