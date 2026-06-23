import React, { useState } from 'react';
import toast from 'react-hot-toast';
import { Save, Send } from 'lucide-react';
import Card from './Card';

export default function EmailSettings(): React.ReactElement {
  const [testing, setTesting] = useState(false);

  const handleTestEmail = async () => {
    setTesting(true);
    await new Promise((r) => setTimeout(r, 1500)); // Simulate sending
    setTesting(false);
    toast.success("Test email sent successfully!");
  };

  const handleSave = () => {
    toast.success("Email settings saved!");
  };

  return (
    <Card>
      <h3 className="text-xl font-bold text-white mb-4">Email Notification Settings</h3>
      <form className="space-y-4 text-sm">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="label-style">SMTP Server</label>
            <input type="text" className="input-style" defaultValue="smtp.gmail.com" />
          </div>
          <div>
            <label className="label-style">Port</label>
            <input type="number" className="input-style" defaultValue="587" />
          </div>
        </div>
        <div>
          <label className="label-style">Sender's Email Address</label>
          <input type="email" className="input-style" defaultValue="your-nvr@gmail.com" />
        </div>
        <div>
          <label className="label-style">Password</label>
          <input type="password" className="input-style" defaultValue="************" />
        </div>
        <div>
          <label className="label-style">Recipient's Email Address</label>
          <input type="email" className="input-style" defaultValue="alerts@example.com" />
        </div>

        <div className="flex justify-end items-center gap-4 pt-4">
          <button type="button" onClick={handleTestEmail} className="btn-secondary flex items-center" disabled={testing}>
            <Send size={16} className={`mr-2 ${testing ? 'animate-ping' : ''}`} />
            {testing ? "Sending..." : "Send Test Email"}
          </button>
          <button type="button" onClick={handleSave} className="btn-primary flex items-center">
            <Save size={16} className="mr-2" /> Save Changes
          </button>
        </div>
      </form>
    </Card>
  );
}
