import { SlidersHorizontal } from 'lucide-react';

const colorMap = {
  Person: 'bg-blue-400', Car: 'bg-purple-400', Dog: 'bg-yellow-400',
  Cat: 'bg-green-400', Bicycle: 'bg-pink-400', Truck: 'bg-indigo-400',
  default: 'bg-gray-400',
};

const FilterSection = ({ title, children }) => (
  <div className="py-4">
    <h4 className="font-semibold text-gray-300 mb-3 text-sm">{title}</h4>
    {children}
  </div>
);

export default function EventFilter({ filters, onFilterChange, availableCameras, availableEventTypes }) {
  const handleCheckboxChange = (filterKey, value) => {
    const currentValues = filters[filterKey];
    const newValues = currentValues.includes(value)
      ? currentValues.filter(item => item !== value)
      : [...currentValues, value];
    onFilterChange({ [filterKey]: newValues });
  };
  
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 sticky top-6">
      <div className="flex items-center pb-4 border-b border-gray-700">
        <SlidersHorizontal size={18} className="mr-3 text-gray-400" />
        <h3 className="text-lg font-bold text-white">Event Filters</h3>
      </div>
      
      <div className="text-sm divide-y divide-gray-700">
        <FilterSection title="Date">
          <input type="date" value={filters.date} onChange={e => onFilterChange({ date: e.target.value })} className="input-style" />
        </FilterSection>

        <FilterSection title="Favorites">
         <label className="flex items-center space-x-3 cursor-pointer">
            <input type="checkbox" checked={filters.showFavoritesOnly} onChange={e => onFilterChange({ showFavoritesOnly: e.target.checked })} className="checkbox-style" />
            <span className="text-gray-300">Favorites Only</span>
          </label>
        </FilterSection>

        <FilterSection title="Cameras">
          <div className="space-y-2 max-h-40 overflow-y-auto">
            {availableCameras.map(camera => (
              <label key={camera} className="flex items-center space-x-3 cursor-pointer">
                <input type="checkbox" checked={filters.cameras.includes(camera)} onChange={() => handleCheckboxChange('cameras', camera)} className="checkbox-style" />
                <span className="text-gray-300">{camera}</span>
              </label>
            ))}
          </div>
        </FilterSection>
        
        <FilterSection title="Event Types">
          <div className="space-y-2 max-h-40 overflow-y-auto">
            {availableEventTypes.map(type => (
              <label key={type} className="flex items-center space-x-3 cursor-pointer">
                <input type="checkbox" checked={filters.eventTypes.includes(type)} onChange={() => handleCheckboxChange('eventTypes', type)} className="checkbox-style" />
                <span className={`w-2.5 h-2.5 rounded-full ${colorMap[type] || colorMap.default}`}></span>
                <span className="text-gray-300">{type}</span>
              </label>
            ))}
          </div>
        </FilterSection>
      </div>
    </div>
  );
}