/**
 * CTA Restore Script
 * Recupera dados do perfil do backend (via IP) se o localStorage estiver vazio.
 * Essencial para usuários que limpam cache ou trocam de dispositivo.
 */

(function () {
    console.log('🔄 [CTA RESTORE] Inicializando script de restauração...');

    async function checkAndRestoreData() {
        // 1. Verificar se já temos dados
        const profileJson = localStorage.getItem('instagram_profile');
        const username = localStorage.getItem('username') || localStorage.getItem('espiado_username');

        if (profileJson && username) {
            // console.log('✅ [CTA RESTORE] Dados já existem no localStorage.');
            return;
        }

        console.log('⚠️ [CTA RESTORE] LocalStorage vazio ou incompleto. Tentando recuperar do backend via IP...');

        try {
            // 2. Obter IP
            const ipResponse = await fetch('/api/get-ip.php'); // Assumindo rota existente usada na home
            const ipData = await ipResponse.json();

            if (!ipData.success || !ipData.ip) {
                console.warn('⚠️ [CTA RESTORE] Não foi possível obter o IP.');
                return;
            }

            const clientIP = ipData.ip;
            // console.log('🔍 [CTA RESTORE] IP detectado:', clientIP);

            // 3. Consultar Leads API
            // Usa o mesmo endpoint que a home usa para verificar bloqueio
            const statusResponse = await fetch(`/api/leads.php?action=check_status_by_ip&ip=${encodeURIComponent(clientIP)}`);
            const statusData = await statusResponse.json();

            if (statusData.success && statusData.exists && statusData.leadData) {
                console.log('✅ [CTA RESTORE] Lead encontrado no backend!');

                const lead = statusData.leadData;
                const profile = lead.lastSpiedProfile || lead.spiedProfile;

                if (profile && profile.username) {
                    const cleanUsername = profile.username.replace(/^@+/, '').trim();

                    // 4. Restaurar LocalStorage
                    localStorage.setItem('espiado_username', cleanUsername);
                    localStorage.setItem('username', cleanUsername);
                    localStorage.setItem('searched_profile', cleanUsername);
                    localStorage.setItem('instagram_profile', JSON.stringify(profile));

                    // Flag opcional para indicar que foi restaurado
                    localStorage.setItem('restored_from_backend', 'true');

                    console.log('💾 [CTA RESTORE] Dados restaurados com sucesso para:', cleanUsername);

                    // 5. Forçar atualização visual (recarregar ou chamar profile_fix)
                    // Se o profile_fix.js estiver rodando, ele deve pegar as mudanças automaticamente na próxima iteração/observer.
                    // Mas podemos forçar um reload suave se a página estiver muito quebrada visuamente.
                    // Por enquanto, confiamos no profile_fix.js.

                    // Tentar disparar evento de storage para outros scripts ouvirem
                    window.dispatchEvent(new Event('storage'));

                } else {
                    console.warn('⚠️ [CTA RESTORE] Lead encontrado mas sem perfil válido.', lead);
                }
            } else {
                console.log('ℹ️ [CTA RESTORE] Nenhum lead encontrado para este IP.');
            }

        } catch (error) {
            console.error('❌ [CTA RESTORE] Erro ao tentar restaurar dados:', error);
        }
    }

    // Executar
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', checkAndRestoreData);
    } else {
        checkAndRestoreData();
    }
})();
