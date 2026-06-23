import React, { useState, useEffect } from 'react';
import { apiFetch } from '../lib/api';
import { useParams, useHistory, Link } from 'react-router-dom';
import { useWebRTC } from '../hooks/useWebRTC';
import { ArrowLeft } from 'lucide-react';
import toast from 'react-hot-toast';

import PTZControls from '../components/PTZControls';
import PTZPresets from '../components/PTZPresets';

interface Camera {
  id: string;
  name: string;
  online: boolean;
  ptz: boolean;
  activity: boolean;
}

const PTZ = () => {
  const { cameraId } = useParams<{cameraId: string}>();
  const history = useHistory(); // Hook for programmatic navigation
  const [camera, setCamera] = useState<Camera | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [presets, _setPresets] = useState(['Home', 'Entrance', 'Parking Lot', 'Rooftop']);
  const [currentPreset, setCurrentPreset] = useState('Home');

  useEffect(() => {
    const initialize = async () => {
      // If we land on the page without a camera ID...
      if (!cameraId) {
        try {
          const response = await apiFetch('/api/cameras');
          const cameras: Camera[] = await response.json();
          // Find the first camera that is online and has PTZ
          const firstPtzCamera = cameras.find(c => c.ptz && c.online);

          if (firstPtzCamera) {
            // ...and redirect to its specific PTZ page.
            toast.success(`Loading first available PTZ camera: ${firstPtzCamera.name}`);
            history.replace(`/ptz/${firstPtzCamera.id}`);
          } else {
            setError("No online PTZ cameras available.");
            setLoading(false);
          }
        } catch (_e) {
          setError("Failed to fetch camera list.");
          setLoading(false);
        }
      } else {
        // If we DO have a camera ID, fetch its data
        try {
          setLoading(true);
          const response = await apiFetch('/api/cameras');
          const cameras: Camera[] = await response.json();
          const selectedCamera = cameras.find((c: Camera) => c.id === cameraId);
          setCamera(selectedCamera || null);
        } catch (_err) {
          setError("Failed to fetch camera data");
        } finally {
          setLoading(false);
        }
      }
    };
    initialize();
  }, [cameraId, history]);

  const { videoRef, isConnecting, error: streamError } = useWebRTC(camera);

  // ... (handleMove, handleZoom, handleRecallPreset functions remain the same)
  const handleMove = (direction: string) => toast(`Moving ${direction.toUpperCase()}`);
  const handleZoom = (direction: number) => toast(`Zooming ${direction > 0 ? 'In' : 'Out'}`);
  const handleRecallPreset = (preset: string) => toast(`Recalling preset: ${preset}`);

  if (loading) {
    return <div className="text-white text-center p-8">Finding PTZ Camera...</div>;
  }

  if (error) {
    return <div className="text-red-400 text-center p-8">{error}</div>;
  }

  if (!camera) {
    return <div className="text-red-400 text-center p-8">Camera with ID '{cameraId}' not found.</div>;
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 text-gray-300">
      <div className="flex-shrink-0">
        <Link to="/live" className="flex items-center text-blue-400 hover:text-blue-300 mb-4">
          <ArrowLeft size={18} className="mr-2"/> Back to Live View
        </Link>
        <h1 className="text-3xl font-bold text-white">PTZ Control: {camera.name}</h1>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-4">
          <div className="lg:col-span-2 bg-black rounded-md relative">
            <video ref={videoRef} playsInline muted className="w-full h-full object-contain" />
            {(isConnecting || streamError) &&
              <div className="absolute inset-0 flex items-center justify-center text-white">
                {streamError ? `Stream Error: ${streamError}` : 'Connecting...'}
              </div>
            }
          </div>

          <div className="lg:col-span-1 flex flex-col gap-6">
            <PTZControls onMove={handleMove} onZoom={handleZoom} />
            <PTZPresets
              presets={presets}
              currentPreset={currentPreset}
              onPresetChange={setCurrentPreset}
              onRecall={handleRecallPreset}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default PTZ;
