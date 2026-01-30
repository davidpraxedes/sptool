/**
 * Meta Pixel Tracking - Centralized Script
 * Gerencia todos os eventos do Facebook Pixel
 */

(function () {
    // --- CONFIGURAÇÃO ---
    // ⚠️ SUBSTITUA PELO SEU PIXEL ID REAL AQUI
    const META_PIXEL_ID = 'YOUR_PIXEL_ID_HERE';

    // --- CÓDIGO BASE DO META PIXEL ---
    !function (f, b, e, v, n, t, s) {
        if (f.fbq) return; n = f.fbq = function () {
            n.callMethod ?
            n.callMethod.apply(n, arguments) : n.queue.push(arguments)
        };
        if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = '2.0';
        n.queue = []; t = b.createElement(e); t.async = !0;
        t.src = v; s = b.getElementsByTagName(e)[0];
        s.parentNode.insertBefore(t, s)
    }(window, document, 'script',
        'https://connect.facebook.net/en_US/fbevents.js');

    // Inicializa o Pixel
    fbq('init', META_PIXEL_ID);

    // --- LOG (Apenas para dev/debug, remover em prod se quiser limpar console) ---
    console.log(`📡 Meta Pixel Inicializado (${META_PIXEL_ID})`);

    // --- 1. PageView GLOBAL (Dispara em todas as páginas onde o script está) ---
    fbq('track', 'PageView');

    // --- DETECÇÃO DE PÁGINA E EVENTOS ESPECÍFICOS ---
    const path = window.location.pathname;

    // 2. InitiateCheckout (Checkout)
    if (path.includes('checkout')) {
        fbq('track', 'InitiateCheckout');
        console.log('🚀 Evento Disparado: InitiateCheckout');
    }

    // 3. Purchase (Páginas de Pagamento/Sucesso)
    // Dispara quando o usuário chega na tela de multibanco ou mbway (gerou pedido)
    else if (path.includes('multibanco-payment') || path.includes('mbway-payment')) {
        fbq('track', 'Purchase', {
            value: 12.90,
            currency: 'EUR',
            content_name: 'InstaSpy Acesso Vitalício'
        });
        console.log('🚀 Evento Disparado: Purchase');
    }

})();
