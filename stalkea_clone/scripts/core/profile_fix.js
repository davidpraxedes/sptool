/**
 * Profile Display Fix Script
 * Garante que o perfil correto seja exibido para usuários recorrentes,
 * substituindo quaisquer fallbacks ("André", "Cardoso") pelos dados reais do localStorage.
 */

(function () {
    console.log('🔧 [PROFILE FIX] Inicializando script de correção de perfil...');

    // Configuração
    const TARGET_TEXTS = ['André', 'Cardoso', 'Pessoa Investigada', 'qualquer pessoa'];
    const MISSING_DATA_LOG_COOLDOWN_MS = 15000;
    const SELECTORS = {
        username: ['.username-display', '.profile-card-name', '.map-container .profile-card-name'],
        fullName: ['.profile-card-username', '.chat-name', 'h2.profile-card-username'],
        images: ['.profile-card-avatar-img', '.location-profile-img', '.chat-avatar', '#profilePic'],
        genericText: ['.feature-desc', '.tool-title', '.control-title', '.attention-box-text', '.pricing-benefit-text', '.pricing-section p', '.pricing-section h2', '.pricing-section h3']
    };
    let lastMissingDataLogAt = 0;

    function safeParse(value) {
        if (!value) return null;
        try {
            return JSON.parse(value);
        } catch {
            return null;
        }
    }

    function normalizeProfile(rawProfile) {
        if (!rawProfile || typeof rawProfile !== 'object') {
            return null;
        }

        // Alguns fluxos salvam a resposta da API dentro de "data".
        if (rawProfile.data && typeof rawProfile.data === 'object') {
            return normalizeProfile(rawProfile.data);
        }

        // Alguns fluxos de limite salvam dentro destes campos.
        if (rawProfile.lastSpiedProfile && typeof rawProfile.lastSpiedProfile === 'object') {
            return normalizeProfile(rawProfile.lastSpiedProfile);
        }
        if (rawProfile.spiedProfile && typeof rawProfile.spiedProfile === 'object') {
            return normalizeProfile(rawProfile.spiedProfile);
        }

        return rawProfile;
    }

    // Função para obter dados do perfil
    function getProfileData() {
        try {
            // 1. Tentar recuperar perfil de múltiplas chaves conhecidas
            let profile = null;
            const profileStorageKeys = ['instagram_profile', 'temp_profile_info', 'lead_data', 'last_spied_profile', 'spied_profile'];
            for (const key of profileStorageKeys) {
                const parsed = normalizeProfile(safeParse(localStorage.getItem(key)));
                if (parsed) {
                    profile = parsed;
                    break;
                }
            }

            // 2. Tentar localStorage/sessionStorage para username
            const usernameStorageKeys = ['espiado_username', 'username', 'searched_profile'];
            let username = '';
            for (const key of usernameStorageKeys) {
                const candidate = localStorage.getItem(key) || sessionStorage.getItem(key);
                if (candidate) {
                    username = candidate;
                    break;
                }
            }

            // Fluxo do home guarda em "searchedProfile" no sessionStorage.
            if (!username) {
                username = sessionStorage.getItem('searchedProfile') || '';
            }
            if (username) username = username.replace(/^@+/, '').trim();

            // 3. Tentar URL params
            if (!username) {
                const urlParams = new URLSearchParams(window.location.search);
                username = urlParams.get('username');
                if (username) username = username.replace(/^@+/, '').trim();
            }

            // Se não temos NADA, não podemos corrigir
            if (!username && !profile) {
                const now = Date.now();
                if (now - lastMissingDataLogAt > MISSING_DATA_LOG_COOLDOWN_MS) {
                    console.warn('⚠️ [PROFILE FIX] Nenhum dado de perfil encontrado para correção.');
                    lastMissingDataLogAt = now;
                }
                return null;
            }

            // Construir objeto de dados
            const data = {
                username: username || (profile ? profile.username : ''),
                fullName: (profile && profile.full_name) ? profile.full_name : '',
                profilePic: (profile && (profile.profile_pic_url || profile.profile_pic_url_hd)) ? (profile.profile_pic_url || profile.profile_pic_url_hd) : ''
            };

            if (!data.username && profile && profile.username) {
                data.username = String(profile.username).replace(/^@+/, '').trim();
            }

            // Derivar primeiro nome
            data.firstName = data.fullName ? data.fullName.split(' ')[0] : (data.username || 'o perfil');

            // Fallback se firstName for vazio
            if (!data.firstName) data.firstName = 'o perfil';

            return data;
        } catch (e) {
            console.error('❌ [PROFILE FIX] Erro ao recuperar dados:', e);
            return null;
        }
    }

    // Função principal de correção
    function fixProfileDisplay() {
        const data = getProfileData();
        if (!data) return;

        // console.log('🔧 [PROFILE FIX] Aplicando correções com:', data);

        // 1. Corrigir Usernames (@usuario)
        SELECTORS.username.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => {
                if (data.username && (!el.textContent.includes(data.username) || el.textContent.includes('pessoa_investigada'))) {
                    el.textContent = '@' + data.username;
                    // console.log(`✅ [PROFILE FIX] Username corrigido em ${selector}`);
                }
            });
        });

        // 2. Corrigir Nomes Completos / Primeiros Nomes
        SELECTORS.fullName.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => {
                // Se o elemento contiver algum dos textos alvo ou estiver vazio/genérico
                const currentText = el.textContent.trim();
                const needsFix = TARGET_TEXTS.some(t => currentText.includes(t)) || currentText === 'Pessoa Investigada' || currentText === '';

                if (needsFix) {
                    // Se for chat-name ou display, usa primeiro nome. Se for card-username, usa full name ou username
                    if (selector.includes('chat') || selector.includes('display')) {
                        el.textContent = data.firstName;
                    } else {
                        el.textContent = data.fullName || data.username || 'Perfil Investigado';
                    }
                }
            });
        });

        // 3. Corrigir Imagens
        if (data.profilePic) {
            // Aplicar proxy se necessário
            let imgUrl = data.profilePic;
            if (window.getProxyImageUrl && !imgUrl.includes('image-proxy')) {
                // imgUrl = window.getProxyImageUrl(imgUrl); // Pode causar loop se a função falhar
            }

            SELECTORS.images.forEach(selector => {
                document.querySelectorAll(selector).forEach(el => {
                    // Para elementos IMG
                    if (el.tagName === 'IMG') {
                        // Se a src for diferente ou se for a imagem padrão
                        if ((!el.src.includes(imgUrl) && !el.src.includes('proxy')) || el.src.includes('perfil-espiado.jpeg') || el.src.includes('undefined')) {
                            el.src = imgUrl;
                            // Resetar display caso tenha sido ocultado por erro
                            el.style.display = '';
                            if (el.nextElementSibling && el.nextElementSibling.tagName === 'DIV' && el.nextElementSibling.innerHTML.includes('svg')) {
                                el.nextElementSibling.style.display = 'none'; // Esconder fallback svg
                            }
                        }
                    }
                    // Para background-image (chat avatar)
                    else if (getComputedStyle(el).backgroundImage.includes('perfil-espiado') || getComputedStyle(el).backgroundImage.includes('none')) {
                        el.style.backgroundImage = `url('${imgUrl}')`;
                    }
                });
            });
        }

        // 4. Corrigir Textos Genéricos (Feature descriptions, titles, etc)
        SELECTORS.genericText.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => {
                // Substituir "André" ou "Cardoso" pelo nome correto
                TARGET_TEXTS.forEach(target => {
                    if (el.innerHTML.includes(target)) {
                        const regex = new RegExp(target, 'g');
                        // Preservar HTML (spans, brs) fazendo replace no innerHTML com cuidado
                        // Mas para segurança, melhor substituir apenas texto se possível, ou usar replace cuidadoso
                        el.innerHTML = el.innerHTML.replace(regex, data.firstName);
                    }
                });

                // Substituir especificamente dentro de spans com classe username-display (reforço)
                const displays = el.querySelectorAll('.username-display');
                displays.forEach(d => d.textContent = data.firstName);
            });
        });

        // 5. Correção Especial para o Modal de Limite (home.html)
        const modalTitle = document.querySelector('#blockedOverlay h2');
        if (modalTitle && document.querySelector('#blockedOverlay p')) {
            const paragraphs = document.querySelectorAll('#blockedOverlay p');
            paragraphs.forEach(p => {
                if (p.textContent.includes('André') || p.textContent.includes('Cardoso')) {
                    p.innerHTML = p.innerHTML.replace('André', data.firstName).replace('Cardoso', '');
                }
            });
        }
    }

    let isFixScheduled = false;
    function scheduleFix() {
        if (isFixScheduled) return;
        isFixScheduled = true;
        requestAnimationFrame(() => {
            isFixScheduled = false;
            fixProfileDisplay();
        });
    }

    // Executar imediatamente
    fixProfileDisplay();

    // Observer para mudanças no DOM (modais abrindo, conteúdo carregando)
    const observer = new MutationObserver((mutations) => {
        let shouldUpdate = false;
        mutations.forEach(mutation => {
            if (mutation.addedNodes.length > 0 || mutation.type === 'attributes') {
                shouldUpdate = true;
            }
        });
        if (shouldUpdate) scheduleFix();
    });

    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'src'] });
    } else {
        document.addEventListener('DOMContentLoaded', () => {
            observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'src'] });
            scheduleFix();
        });
    }

    // Polling de segurança (para casos onde o observer falha ou scripts demoram)
    setInterval(scheduleFix, 2500);

})();
