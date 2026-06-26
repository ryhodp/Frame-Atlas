import React, { useEffect, useState } from 'react';


export default function SyncManager() {
  const [folders, setFolders] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState(null);
  const [selectedFolderName, setSelectedFolderName] = useState('');
  const [currentSyncFolder, setCurrentSyncFolder] = useState(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState(null);
  const [errors, setErrors] = useState([]);

  // Load available folders
  useEffect(() => {
    fetchFolders();
    checkSyncSettings();
  }, []);

  // Poll sync status while syncing
  useEffect(() => {
    let interval;
    if (syncing) {
      interval = setInterval(() => {
        fetch('/api/sync/status')
          .then(r => r.json())
          .then(data => {
            setSyncStatus(data);
            if (!data.in_progress) {
              setSyncing(false);
              setErrors(data.errors || []);
            }
          });
      }, 500);
    }
    return () => clearInterval(interval);
  }, [syncing]);

  const fetchFolders = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/folders');
      const data = await res.json();
      setFolders(data.folders || []);
    } catch (err) {
      console.error('Failed to fetch folders:', err);
    } finally {
      setLoading(false);
    }
  };

  const checkSyncSettings = async () => {
    try {
      const res = await fetch('/api/sync/settings');
      const data = await res.json();
      if (data.folder_id) {
        setCurrentSyncFolder({
          id: data.folder_id,
          name: data.folder_name
        });
      }
    } catch (err) {
      console.error('Failed to check sync settings:', err);
    }
  };

  const handleSetFolder = async () => {
    if (!selectedFolder) return;

    try {
      const res = await fetch('/api/sync/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          folder_id: selectedFolder,
          folder_name: selectedFolderName
        })
      });

      if (res.ok) {
        setCurrentSyncFolder({
          id: selectedFolder,
          name: selectedFolderName
        });
        setSelectedFolder(null);
        setSelectedFolderName('');
      }
    } catch (err) {
      console.error('Failed to set folder:', err);
    }
  };

  const handleStartSync = async () => {
    if (!currentSyncFolder) {
      alert('Please select a folder first');
      return;
    }

    setSyncing(true);
    setSyncStatus({ in_progress: true, processed: 0, total: 0, errors: [] });
    setErrors([]);

    try {
      const res = await fetch('/api/sync/start', {
        method: 'POST'
      });
      const data = await res.json();
      if (!data.success) {
        setErrors([data.error || 'Failed to start sync']);
        setSyncing(false);
      }
    } catch (err) {
      setErrors(['Failed to start sync: ' + err.message]);
      setSyncing(false);
    }
  };

  const progressPercent = syncStatus && syncStatus.total > 0
    ? Math.round((syncStatus.processed / syncStatus.total) * 100)
    : 0;

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-2xl font-bold mb-6 text-gray-800">Google Drive Sync</h2>

        {/* Folder Selection */}
        <div className="mb-8 pb-8 border-b">
          <h3 className="text-lg font-semibold text-gray-700 mb-4">1. Select Folder to Sync</h3>

          {currentSyncFolder && (
            <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded">
              <p className="text-sm text-gray-600">Currently syncing:</p>
              <p className="text-lg font-semibold text-green-700">📁 {currentSyncFolder.name}</p>
            </div>
          )}

          {loading ? (
            <p className="text-gray-500">Loading folders...</p>
          ) : (
            <div className="space-y-3">
              <select
                value={selectedFolder || ''}
                onChange={(e) => {
                  const folderId = e.target.value;
                  const folderObj = folders.find(f => f.id === folderId);
                  setSelectedFolder(folderId);
                  setSelectedFolderName(folderObj?.name || '');
                }}
                className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Choose a folder...</option>
                {folders.map(folder => (
                  <option key={folder.id} value={folder.id}>
                    📁 {folder.name}
                  </option>
                ))}
              </select>

              {selectedFolder && (
                <button
                  onClick={handleSetFolder}
                  className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
                >
                  Set This Folder
                </button>
              )}
            </div>
          )}
        </div>

        {/* Sync Controls */}
        <div className="mb-8">
          <h3 className="text-lg font-semibold text-gray-700 mb-4">2. Sync Images</h3>

          <button
            onClick={handleStartSync}
            disabled={syncing || !currentSyncFolder}
            className="w-full px-6 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white font-semibold rounded-lg hover:shadow-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {syncing ? 'Syncing...' : 'Sync Now'}
          </button>
        </div>

        {/* Progress Bar */}
        {syncing && syncStatus && (
          <div className="mb-8">
            <div className="mb-2 flex justify-between items-center">
              <span className="text-sm font-medium text-gray-700">Progress</span>
              <span className="text-sm text-gray-600">
                {syncStatus.processed} / {syncStatus.total}
              </span>
            </div>

            <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
              <div
                className="bg-gradient-to-r from-blue-500 to-indigo-600 h-full transition-all duration-300"
                style={{ width: `${progressPercent}%` }}
              ></div>
            </div>

            <p className="mt-2 text-sm text-gray-600">
              {syncStatus.current_file && `Currently: ${syncStatus.current_file.substring(0, 40)}...`}
            </p>
          </div>
        )}

        {/* Completion Message */}
        {!syncing && syncStatus && syncStatus.processed > 0 && (
          <div className="mb-8 p-4 bg-blue-50 border border-blue-200 rounded">
            <p className="text-lg font-semibold text-blue-700">
              ✓ Sync Complete!
            </p>
            <p className="text-sm text-gray-600 mt-1">
              Imported {syncStatus.processed} images
            </p>
          </div>
        )}

        {/* Error List */}
        {errors.length > 0 && (
          <div className="mb-8">
            <h3 className="text-lg font-semibold text-red-700 mb-3">Issues During Sync</h3>
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <ul className="space-y-2">
                {errors.map((error, idx) => (
                  <li key={idx} className="text-sm text-red-700 flex items-start">
                    <span className="mr-2">⚠️</span>
                    <span>{error}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
