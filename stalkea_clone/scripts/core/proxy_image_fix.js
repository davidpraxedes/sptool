(function () {
    'use strict';

    const DEAD_PROXY_MARKERS = [
        'proxt-insta.projetinho-solo.workers.dev',
        'proxt-insta'
    ];
    const SAFE_PROXY_HOST = 'images.weserv.nl';
    const SAME_ORIGIN_PROXY_PATH = '/api/image-proxy';

    const originalGetProxyImageUrl =
        typeof window.getProxyImageUrl === 'function' ? window.getProxyImageUrl : null;
    const originalGetProxyImageUrlLight =
        typeof window.getProxyImageUrlLight === 'function' ? window.getProxyImageUrlLight : null;

    function safeDecode(value) {
        if (typeof value !== 'string') return '';
        try {
            return decodeURIComponent(value);
        } catch {
            return value;
        }
    }

    function isHttpUrl(value) {
        return typeof value === 'string' && /^https?:\/\//i.test(value.trim());
    }

    function decodeWrappedImageUrl(rawUrl) {
        if (typeof rawUrl !== 'string') return '';

        let current = rawUrl.trim();
        if (!current) return '';

        for (let i = 0; i < 3; i++) {
            let parsed;
            try {
                parsed = new URL(current, window.location.origin);
            } catch {
                return current;
            }

            const hasUrlParam = parsed.searchParams.has('url');
            const canUnwrap =
                hasUrlParam &&
                (parsed.pathname.includes('/_next/image') ||
                    parsed.hostname.includes('workers.dev') ||
                    parsed.pathname.includes('image-proxy.php'));

            if (!canUnwrap) {
                return current;
            }

            const decoded = safeDecode(parsed.searchParams.get('url') || '');
            if (!decoded || decoded === current) {
                return current;
            }

            current = decoded.trim();
        }

        return current;
    }

    function normalizeUrl(rawUrl) {
        const decoded = decodeWrappedImageUrl(rawUrl);
        if (!decoded) return '';
        if (decoded.startsWith('//')) return `https:${decoded}`;
        return decoded;
    }

    function isDeadProxyUrl(url) {
        if (typeof url !== 'string') return false;
        const lower = url.toLowerCase();
        return DEAD_PROXY_MARKERS.some((marker) => lower.includes(marker));
    }

    function isAlreadySafeProxy(url) {
        try {
            const parsed = new URL(url, window.location.origin);
            const isSameOriginProxy =
                parsed.origin === window.location.origin &&
                parsed.pathname === SAME_ORIGIN_PROXY_PATH;
            return (
                parsed.hostname === SAFE_PROXY_HOST ||
                url.includes('image-proxy.php') ||
                isSameOriginProxy
            );
        } catch {
            return false;
        }
    }

    function buildSameOriginProxyUrl(url, isLight) {
        const normalized = normalizeUrl(url);
        if (!normalized) return normalized;
        if (!isHttpUrl(normalized)) return normalized;

        const params = new URLSearchParams({
            url: normalized
        });
        params.set('size', isLight ? 'light' : 'full');
        return `${SAME_ORIGIN_PROXY_PATH}?${params.toString()}`;
    }

    function shouldProxyViaSafeFallback(url) {
        if (!isHttpUrl(url)) return false;
        try {
            const host = new URL(url).hostname.toLowerCase();
            return (
                host.includes('instagram.com') ||
                host.includes('cdninstagram.com') ||
                host.includes('fbcdn.net')
            );
        } catch {
            return false;
        }
    }

    function buildSafeProxyUrl(url, isLight) {
        const normalized = normalizeUrl(url);
        if (!normalized) return normalized;
        if (!isHttpUrl(normalized)) return normalized;
        if (isAlreadySafeProxy(normalized)) return normalized;
        if (!shouldProxyViaSafeFallback(normalized) && !isDeadProxyUrl(normalized)) return normalized;

        const withoutProtocol = normalized.replace(/^https?:\/\//i, '');
        const params = new URLSearchParams({
            url: withoutProtocol,
            w: isLight ? '220' : '640',
            fit: 'cover',
            output: 'webp'
        });
        return `https://${SAFE_PROXY_HOST}/?${params.toString()}`;
    }

    function createPatchedProxyFn(originalFn, isLight) {
        return function patchedProxyImageUrl(inputUrl) {
            const normalizedInput = normalizeUrl(inputUrl);
            const safeFallback = buildSafeProxyUrl(normalizedInput, isLight);
            const sameOriginProxy = buildSameOriginProxyUrl(normalizedInput, isLight);

            if (typeof originalFn !== 'function') {
                return sameOriginProxy || safeFallback || normalizedInput || inputUrl;
            }

            try {
                const originalOutput = originalFn(normalizedInput || inputUrl);
                const rawOutput = typeof originalOutput === 'string' ? originalOutput : '';

                if (isDeadProxyUrl(rawOutput)) {
                    return sameOriginProxy || safeFallback || normalizedInput || inputUrl;
                }

                const normalizedOutput = normalizeUrl(rawOutput);

                if (!normalizedOutput || isDeadProxyUrl(normalizedOutput)) {
                    return sameOriginProxy || safeFallback || normalizedInput || inputUrl;
                }

                if (isAlreadySafeProxy(normalizedOutput)) {
                    return normalizedOutput;
                }

                if (shouldProxyViaSafeFallback(normalizedOutput)) {
                    return buildSameOriginProxyUrl(normalizedOutput, isLight) || safeFallback || normalizedOutput;
                }

                if (shouldProxyViaSafeFallback(normalizedInput)) {
                    return sameOriginProxy || safeFallback || normalizedOutput;
                }

                return normalizedOutput;
            } catch {
                return sameOriginProxy || safeFallback || normalizedInput || inputUrl;
            }
        };
    }

    window.getProxyImageUrl = createPatchedProxyFn(originalGetProxyImageUrl, false);
    window.getProxyImageUrlLight = createPatchedProxyFn(originalGetProxyImageUrlLight, true);

    if (window.instagramAPI && typeof window.instagramAPI === 'object') {
        window.instagramAPI.getProxyImageUrl = window.getProxyImageUrl;
        window.instagramAPI.getProxyImageUrlLight = window.getProxyImageUrlLight;
    }
})();
