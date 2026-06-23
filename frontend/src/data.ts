import type { Camera, Event, User } from './types';

export const MOCK_CAMERAS: Camera[] = [
  { id: 1, name: 'Driveway', status: 'online', model: 'Hikvision DS-2CD2043G2-I', ip: '192.168.1.101:554', protocol: 'ONVIF', resolution: '4MP (2560x1440)', fps: 30, features: ['PTZ', 'Audio'], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Driveway', activity: true },
  { id: 2, name: 'Front Door', status: 'online', model: 'Dahua IPC-HDW2431T-AS', ip: '192.168.1.102:554', protocol: 'ONVIF', resolution: '4MP (2560x1440)', fps: 25, features: ['Audio'], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Front+Door' },
  { id: 3, name: 'Backyard', status: 'online', model: 'Axis P5655-E', ip: '192.168.1.103:554', protocol: 'ONVIF', resolution: '2MP (1920x1080)', fps: 30, features: ['PTZ', 'Audio'], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Backyard' },
  { id: 4, name: 'Garage', status: 'online', model: 'Amcrest IP5M-T1179EW-AI', ip: '192.168.1.104:554', protocol: 'ONVIF', resolution: '5MP (2592x1944)', fps: 20, features: [], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Garage', activity: true },
  { id: 5, name: 'Kitchen', status: 'offline', model: 'Reolink RLC-810A', ip: '192.168.1.105:554', protocol: 'ONVIF', resolution: '8MP (3840x2160)', fps: 25, features: ['Audio'], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Kitchen' },
  { id: 6, name: 'Living Room', status: 'online', model: 'Wyze Cam v3', ip: '192.168.1.106:554', protocol: 'RTSP', resolution: '1080p (1920x1080)', fps: 20, features: ['Audio'], enabled: true, thumbnail: 'https://placehold.co/600x400/2d3748/9fa6b2?text=Living+Room' },
];

export const MOCK_EVENTS: Event[] = [
  { id: 1, camera: 'Driveway', zone: 'Front Yard', type: 'person', timestamp: new Date(Date.now() - 2 * 60 * 1000), thumbnail: 'https://placehold.co/200x150/1a1a1a/ffffff?text=Person', duration: 15, confidence: 95, favorite: false },
  { id: 2, camera: 'Front Door', zone: 'Front Yard', type: 'vehicle', timestamp: new Date(Date.now() - 5 * 60 * 1000), thumbnail: 'https://placehold.co/200x150/1a1a1a/ffffff?text=Vehicle', duration: 8, confidence: 88, favorite: true },
  { id: 3, camera: 'Garage', zone: 'Driveway', type: 'motion', timestamp: new Date(Date.now() - 15 * 60 * 1000), thumbnail: 'https://placehold.co/200x150/1a1a1a/ffffff?text=Motion', duration: 3, confidence: 82, favorite: false },
  { id: 4, camera: 'Driveway', zone: 'Front Yard', type: 'dog', timestamp: new Date(Date.now() - 25 * 60 * 1000), thumbnail: 'https://placehold.co/200x150/1a1a1a/ffffff?text=Dog', duration: 20, confidence: 91, favorite: false },
];

export const MOCK_USERS: User[] = [
    { id: 1, name: 'Admin', role: 'Administrator', lastLogin: '2025-08-27 09:30:15' },
    { id: 2, name: 'Security Desk', role: 'Operator', lastLogin: '2025-08-27 09:25:40' },
    { id: 3, name: 'Manager', role: 'Viewer', lastLogin: '2025-08-26 15:10:05' },
];


