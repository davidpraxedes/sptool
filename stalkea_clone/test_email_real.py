
import os
import sys

# Adicionar o diretório atual ao path para importar server.py
sys.path.append(os.getcwd())

from server import send_email_via_sendgrid, app

# Mockar contexto se necessário, ou apenas rodar a função
# A função get_config tenta conectar no DB. Se tiver DB URL, funciona.

print("🚀 Iniciando teste de envio de email...")
print("Destino: modderstore2010@gmail.com")

with app.app_context():
    success = send_email_via_sendgrid(
        "modderstore2010@gmail.com",
        "Teste de Configuração - Novo Remetente",
        """
        <div style="font-family: sans-serif; padding: 20px; border: 1px solid #ccc;">
            <h2 style="color: #10B981;">Teste de Envio Bem Sucedido!</h2>
            <p>Este email foi enviado para validar a alteração do remetente.</p>
            <p><strong>Remetente Esperado:</strong> support@brasilconectasolucoes.shop</p>
            <hr>
            <p style="font-size: 12px; color: #888;">Enviado via script de teste (test_email_real.py)</p>
        </div>
        """
    )

if success:
    print("✅ Sucesso! Verifique a caixa de entrada (e spam).")
else:
    print("❌ Falha no envio. Verifique os logs acima.")
