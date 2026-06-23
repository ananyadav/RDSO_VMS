import { X, Camera, Calendar, Clock } from 'lucide-react';

export default function EventPlaybackModal({ event, onClose }) {
  if (!event) return null;

  return (
    // Backdrop
    <div 
      onClick={onClose}
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
    >
      {/* Modal Content */}
      <div 
        onClick={(e) => e.stopPropagation()} // Prevent closing when clicking inside
        className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-3xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <h3 className="text-xl font-bold text-white">{event.type} Detected</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X size={24} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-grow p-4">
          <div className="bg-black aspect-video w-full flex items-center justify-center text-gray-500 rounded-md">
            Video Playback for {event.camera}
          </div>
          <div className="grid grid-cols-3 gap-4 mt-4 text-sm">
            <div className="flex items-center text-gray-300">
              <Camera size={16} className="mr-2 text-blue-400" />
              <strong>Camera:</strong><span className="ml-2">{event.camera}</span>
            </div>
            <div className="flex items-center text-gray-300">
              <Calendar size={16} className="mr-2 text-blue-400" />
              <strong>Date:</strong><span className="ml-2">{new Date(event.timestamp).toLocaleDateString()}</span>
            </div>
            <div className="flex items-center text-gray-300">
              <Clock size={16} className="mr-2 text-blue-400" />
              <strong>Time:</strong><span className="ml-2">{new Date(event.timestamp).toLocaleTimeString()}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}