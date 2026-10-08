# Project Z — versão local 0.1

## Termux

```bash
cd /storage/94CA-12FD/projectz
python -m pip install flask werkzeug
python app.py
```

Abra:
http://127.0.0.1:8080/

O banco `projectz.db` é criado automaticamente.
As fotos ficam em `uploads/`.

Rotas iniciais:
- `/`
- `/login`
- `/register`
- `/home`
- `/profile`
- `/api/status`

Esta versão é deliberadamente local/experimental. A chave de sessão e algumas configurações serão endurecidas quando começarmos a fase de segurança.
