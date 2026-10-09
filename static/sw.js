/* Service worker mínimo: deixa o app instalável e mostra uma tela offline simples.
   Não guarda páginas nem dados de usuário em cache (conteúdo sempre vem do servidor). */
const OFFLINE_HTML = `<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>K — sem conexão</title>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#050506;color:#f5f5f6;font-family:system-ui,sans-serif;text-align:center">
<div><div style="width:64px;height:64px;margin:0 auto 18px;border-radius:18px;background:linear-gradient(145deg,#ff2d40,#8f0a16);display:grid;place-items:center;font-size:36px;font-weight:900">K</div>
<h1 style="font-size:20px;margin:0 0 8px">Sem conexão</h1>
<p style="color:#8a8a93;margin:0 0 20px">Verifique a internet e tente de novo.</p>
<button onclick="location.reload()" style="background:#e11d2e;color:#fff;border:0;border-radius:12px;padding:12px 22px;font-weight:700">Tentar novamente</button></div></body></html>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(
    fetch(event.request).catch(
      () => new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
    )
  );
});
