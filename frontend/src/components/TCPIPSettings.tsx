import React, { useState } from 'react';
import toast from 'react-hot-toast';
import { Save } from 'lucide-react';
import Card from './Card'; // Assuming you have this from previous steps

export default function TCPIPSettings(): React.ReactElement {
  const [formData, setFormData] = useState({
    hostname: 'NVR-System',
    dhcp: true,
    ipAddress: '192.168.1.100',
    subnetMask: '255.255.255.0',
    gateway: '192.168.1.1',
    dns1: '8.8.8.8',
    dns2: '8.8.4.4',
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const target = e.target as HTMLInputElement; // Cast to access checked property
    const { name, value, type } = target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? target.checked : value,
    }));
  };

  const handleSave = () => {
    toast.success('TCP/IP settings saved successfully!');
  };

  return (
    <Card>
      <h3 className="text-xl font-bold text-white mb-4">TCP/IP Configuration</h3>
      <form className="space-y-4 text-sm">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="hostname" className="label-style">Hostname</label>
            <input type="text" id="hostname" name="hostname" value={formData.hostname} onChange={handleChange} className="input-style" />
          </div>
          <div className="flex items-center pt-6">
            <input type="checkbox" id="dhcp" name="dhcp" checked={formData.dhcp} onChange={handleChange} className="checkbox-style" />
            <label htmlFor="dhcp" className="ml-2 text-gray-300">Enable DHCP</label>
          </div>
        </div>

        {!formData.dhcp && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border-t border-gray-700 pt-4">
            <div>
              <label htmlFor="ipAddress" className="label-style">IPv4 Address</label>
              <input type="text" id="ipAddress" name="ipAddress" value={formData.ipAddress} onChange={handleChange} className="input-style" />
            </div>
            <div>
              <label htmlFor="subnetMask" className="label-style">Subnet Mask</label>
              <input type="text" id="subnetMask" name="subnetMask" value={formData.subnetMask} onChange={handleChange} className="input-style" />
            </div>
            <div>
              <label htmlFor="gateway" className="label-style">Default Gateway</label>
              <input type="text" id="gateway" name="gateway" value={formData.gateway} onChange={handleChange} className="input-style" />
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border-t border-gray-700 pt-4">
          <div>
            <label htmlFor="dns1" className="label-style">Preferred DNS Server</label>
            <input type="text" id="dns1" name="dns1" value={formData.dns1} onChange={handleChange} className="input-style" />
          </div>
          <div>
            <label htmlFor="dns2" className="label-style">Alternate DNS Server</label>
            <input type="text" id="dns2" name="dns2" value={formData.dns2} onChange={handleChange} className="input-style" />
          </div>
        </div>
        
        <div className="flex justify-end pt-4">
          <button type="button" onClick={handleSave} className="btn-primary flex items-center">
            <Save size={16} className="mr-2" /> Save Changes
          </button>
        </div>
      </form>
    </Card>
  );
}
