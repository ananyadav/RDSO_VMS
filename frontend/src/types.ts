export type EventType = 'person' | 'vehicle' | 'motion' | 'cat' | 'dog' | 'bicycle' | 'animal' | 'truck';

export interface Camera {
  id: number;
  name: string;
  status: 'online' | 'offline';
  model: string;
  ip: string;
  protocol: 'ONVIF' | 'RTSP';
  resolution: string;
  fps: number;
  features: string[];
  enabled: boolean;
  thumbnail: string;
  activity?: boolean;
}

export interface Event {
  id: number;
  camera: string;
  zone: string;
  type: EventType;
  timestamp: Date;
  thumbnail: string;
  duration: number;
  confidence: number;
  favorite: boolean;
}

export interface User {
    id: number;
    name: string;
    role: 'Administrator' | 'Operator' | 'Viewer';
    lastLogin: string;
}

