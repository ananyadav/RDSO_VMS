// src/hooks/useWebRTC.ts

import { useEffect, useRef, useState } from 'react';

interface Camera {
  id: string;
  name: string;
  online: boolean;
}

export const useWebRTC = (camera: Camera | null, isFullscreen: boolean = false) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!camera || !camera.online) {
      return;
    }

    // Capture non-null camera for use inside callbacks
    const cam = camera;

    setIsConnecting(true);
    setError(null);

    // Stagger connections so multiple cameras on the same page don't all
    // hammer the WebSocket server at the exact same millisecond.
    const staggerMs = Math.floor(Math.random() * 800);
    let cleanup: (() => void) | null = null;
    let cancelled = false;

    const staggerTimeout = setTimeout(() => {
      if (cancelled) return;

      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });

      pc.onconnectionstatechange = () => {
        console.log(`[${cam.name}] Peer Connection State: ${pc.connectionState}`);
        if (pc.connectionState === 'connected') {
          setIsConnecting(false);
        }
        if (pc.connectionState === 'failed') {
          setError('Connection failed.');
          setIsConnecting(false);
        }
      };

      pc.oniceconnectionstatechange = () => {
        console.log(`[${cam.name}] ICE State: ${pc.iceConnectionState}`);
        if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
          setIsConnecting(false);
        }
        if (pc.iceConnectionState === 'failed') {
          setError('ICE connection failed.');
          setIsConnecting(false);
        }
      };

      pc.ontrack = (event) => {
        console.log(`[${cam.name}] Track received`);
        setIsConnecting(false);
        if (videoRef.current) {
          const stream = new MediaStream([event.track]);
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {
            setError('Click to play');
          });
        }
      };

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;
      const ws = new WebSocket(wsUrl);

      ws.onopen = async () => {
        pc.addTransceiver('video', { direction: 'recvonly' });
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const message = {
          cameraId: cam.id,
          sdp: pc.localDescription!.sdp,
          type: pc.localDescription!.type,
          isFullscreen: isFullscreen,
        };
        ws.send(JSON.stringify(message));
      };

      ws.onmessage = async (event) => {
        await pc.setRemoteDescription(JSON.parse(event.data));
      };

      // If no track arrives within 20 seconds, give up and show an error
      const connectionTimeout = setTimeout(() => {
        if (videoRef.current && !videoRef.current.srcObject) {
          setIsConnecting(false);
          setError('Stream timeout — camera may be unreachable');
        }
      }, 20000);

      ws.onerror = () => {
        console.error(`[${cam.name}] WebSocket error`);
        setError('WebSocket connection failed. Check if backend server is running.');
        setIsConnecting(false);
      };

      ws.onclose = (event) => {
        console.log(`[${cam.name}] WebSocket closed:`, event.code, event.reason);
        if (event.code !== 1000) {
          setError(`Connection closed unexpectedly (code: ${event.code})`);
        }
      };

      cleanup = () => {
        clearTimeout(connectionTimeout);
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
        if (pc.connectionState !== 'closed') {
          pc.close();
        }
      };
    }, staggerMs);

    return () => {
      cancelled = true;
      clearTimeout(staggerTimeout);
      if (cleanup) cleanup();
    };
  }, [camera]); // eslint-disable-line react-hooks/exhaustive-deps

  return { videoRef, isConnecting, error };
};
