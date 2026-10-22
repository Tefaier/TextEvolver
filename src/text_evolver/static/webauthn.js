(() => {
    const decode = value => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
    const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    async function post(url, body) {
        const response = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {
            'Content-Type': 'application/json', Accept: 'application/json',
            'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content,
        }, body: JSON.stringify(body)});
        const payload = await response.json();
        if (payload.redirect && response.status === 428) {
            window.location.assign(payload.redirect);
        }
        if (!response.ok) throw new Error(payload.detail || window.teTranslate('Authentication failed or was cancelled. Please try again.'));
        return payload;
    }
    document.addEventListener('click', async event => {
        const button = event.target.closest('[data-webauthn]');
        if (!button) return;
        if (!window.PublicKeyCredential || !navigator.credentials) {
            window.showNotification(window.teTranslate('WebAuthn is not supported by this browser.'));
            return;
        }
        button.disabled = true;
        try {
            const operation = button.dataset.webauthn;
            const isAuthentication = ['login', 'reauthenticate', 'recover'].includes(operation);
            if (operation !== 'register' && !isAuthentication) {
                throw new Error(window.teTranslate('Authentication failed or was cancelled. Please try again.'));
            }
            const name = document.getElementById('passkey-name')?.value.trim();
            if (operation === 'register' && (!name || name.length > 64)) {
                window.showNotification(window.teTranslate('Passkey name must contain 1 to 64 characters.'));
                return;
            }
            const options = await post(`/auth/webauthn/${operation}/options`, {});
            const publicKey = options.publicKey;
            publicKey.challenge = decode(publicKey.challenge);
            if (publicKey.user) publicKey.user.id = decode(publicKey.user.id);
            for (const field of ['allowCredentials', 'excludeCredentials']) {
                if (publicKey[field]) publicKey[field] = publicKey[field].map(c => ({...c, id: decode(c.id)}));
            }
            let credential;
            if (operation === 'register') {
                credential = await navigator.credentials.create({publicKey});
            } else if (isAuthentication) {
                credential = await navigator.credentials.get({publicKey});
            }
            const response = credential.response;
            const serialized = {
                id: credential.id, 
                rawId: encode(credential.rawId), 
                type: credential.type,
                clientExtensionResults: credential.getClientExtensionResults(), 
                response: {clientDataJSON: encode(response.clientDataJSON)}
            };
            if (operation === 'register') {
                serialized.response.attestationObject = encode(response.attestationObject);
                serialized.response.transports = response.getTransports ? response.getTransports() : [];
            } else if (isAuthentication) {
                serialized.response.authenticatorData = encode(response.authenticatorData);
                serialized.response.signature = encode(response.signature);
                serialized.response.userHandle = response.userHandle ? encode(response.userHandle) : null;
            }
            const result = await post(`/auth/webauthn/${operation}/verify`, {flow: options.flow, credential: serialized, name});
            window.location.assign(result.redirect);
        } catch (error) {
            window.showNotification(error.name === 'Error' ? error.message : window.teTranslate('Authentication failed or was cancelled. Please try again.'));
        } finally { button.disabled = false; }
    });
})();
