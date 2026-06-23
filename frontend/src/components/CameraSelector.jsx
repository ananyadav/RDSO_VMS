import { Video } from 'lucide-react';

export default function CameraSelector({ cameras, selected, onSelect }) {
  return (
    <div className="flex items-center">
      <Video size={20} className="text-gray-300 mr-2" />
      <select
        value={selected?.id || ''}
        onChange={(e) => {
          const selectedCam = cameras.find(c => c.id === e.target.value);
          if (selectedCam) onSelect(selectedCam);
        }}
        className="bg-gray-800 border border-gray-600 text-white rounded-md px-2 py-1 focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm"
      >
        {cameras.map(cam => (
          <option key={cam.id} value={cam.id}>
            {cam.name}
          </option>
        ))}
      </select>
    </div>
  );
}
