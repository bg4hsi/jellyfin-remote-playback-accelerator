const state = ngx.shared.jellyfin_state;
const segmentPattern = /^(\/videos\/[0-9A-Fa-f-]+\/hls[^/]*\/main\/)([0-9]+)\.(ts|m4s)$/;

function track(r) {
    const match = segmentPattern.exec(r.uri);
    if (!match) return '-';

    const now = Date.now();
    state.set('prefix', match[1]);
    state.set('current', match[2]);
    state.set('extension', match[3]);
    state.set('track_ms', String(now));
    return match[2];
}

function status(r) {
    const trackMs = Number(state.get('track_ms') || 0);
    const now = Date.now();
    const payload = {
        player: {
            tracked: trackMs > 0,
            prefix: state.get('prefix') || null,
            current: state.get('current') ? Number(state.get('current')) : null,
            extension: state.get('extension') || null,
            track_age_ms: trackMs > 0 ? now - trackMs : null
        }
    };
    r.headersOut['Cache-Control'] = 'no-store';
    r.return(200, JSON.stringify(payload));
}

export default { track, status };
