import { useEffect, useState } from 'react';

export default function ImageDetail({ image, onClose }) {
  const [fullImage, setFullImage] = useState(null);
  const [fullError, setFullError] = useState(false);

  useEffect(() => {
    if (!image) return;
    let objectUrl = null;
    setFullImage(null);
    setFullError(false);

    fetch(`/api/images/${image.id}/full`)
      .then(res => {
        if (!res.ok) throw new Error('full-res failed');
        return res.blob();
      })
      .then(blob => {
        objectUrl = URL.createObjectURL(blob);
        setFullImage(objectUrl);
      })
      .catch(() => setFullError(true));

    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [image?.id]);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  if (!image) return null;

  const categories = {};
  (image.tags || []).forEach(tag => {
    if (!categories[tag.category]) categories[tag.category] = [];
    categories[tag.category].push(tag.value);
  });

  const catLabels = {
    'mood': 'Mood', 'lighting_quality': 'Lighting',
    'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
    'shot_type': 'Shot', 'framing_composition': 'Framing',
    'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
    'source_type': 'Source', 'subject_count': 'Subjects',
    'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
    'era_decade': 'Era', 'camera_format': 'Format',
    'performance_emotion': 'Emotion',
  };

  const catOrder = [
    'mood', 'lighting_quality', 'lighting_color_temperature', 'color_palette',
    'shot_type', 'framing_composition', 'location_type', 'time_of_day_weather',
    'source_type', 'subject_count', 'subject_camera_relationship', 'performance_emotion',
    'genre_aesthetic', 'era_decade', 'camera_format'
  ];

  const hasTags = (image.tags || []).length > 0;

  return (
    <>
      {/* Overlay backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          zIndex: 999,
          animation: 'fadeIn 0.2s ease'
        }}
      />

      {/* Side panel */}
      <div
        style={{
          position: 'fixed', right: 0, top: 0, bottom: 0,
          width: 'clamp(360px, 45%, 600px)',
          background: '#0a0a0b',
          borderLeft: '1px solid rgba(255,255,255,0.065)',
          zIndex: 1000,
          display: 'flex', flexDirection: 'column',
          color: '#efeadd',
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          animation: 'slideInRight 0.3s cubic-bezier(0.16, 1, 0.3, 1)'
        }}
      >
        {/* Header */}
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center'
        }}>
          <span style={{
            fontSize: '13px', color: '#65625a',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
          }}>{image.filename}</span>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#65625a',
              cursor: 'pointer', fontSize: '20px', lineHeight: 1, flexShrink: 0
            }}
          >×</button>
        </div>

        {/* Scrollable content */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {/* Full-res image (falls back to thumbnail while loading) */}
          <div style={{
            background: '#141318',
            minHeight: '200px', maxHeight: '420px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            overflow: 'hidden'
          }}>
            <img
              src={fullImage || image.thumbnail}
              alt={image.filename}
              style={{
                maxWidth: '100%', maxHeight: '420px',
                objectFit: 'contain',
                filter: fullImage ? 'none' : 'blur(0.5px)',
                transition: 'filter 0.3s ease'
              }}
            />
          </div>
          {!fullImage && !fullError && (
            <div style={{
              padding: '6px 20px', fontSize: '10px', color: '#65625a',
              fontFamily: "'JetBrains Mono', monospace"
            }}>
              loading full resolution…
            </div>
          )}
          {fullError && (
            <div style={{ padding: '6px 20px', fontSize: '10px', color: '#cf7152' }}>
              Couldn't load full-res — showing thumbnail
            </div>
          )}

          {/* Metadata */}
          <div style={{ padding: '20px' }}>
            {/* Caption */}
            {image.caption && (
              <p style={{
                fontSize: '13px', lineHeight: '1.5',
                color: '#dcbd76', margin: '0 0 20px'
              }}>
                {image.caption}
              </p>
            )}

            {/* Aspect Ratio & Date */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr',
              gap: '12px', marginBottom: '20px',
              padding: '12px', background: 'rgba(255,255,255,0.02)',
              borderRadius: '8px'
            }}>
              <div>
                <div style={{ fontSize: '9px', fontWeight: 600, color: '#65625a', letterSpacing: '0.08em' }}>ASPECT RATIO</div>
                <div style={{ fontSize: '13px', color: '#efeadd', marginTop: '4px' }}>{image.aspect_ratio}</div>
              </div>
              <div>
                <div style={{ fontSize: '9px', fontWeight: 600, color: '#65625a', letterSpacing: '0.08em' }}>FILENAME</div>
                <div style={{
                  fontSize: '13px', color: '#efeadd', marginTop: '4px',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                }}>{image.filename}</div>
              </div>
            </div>

            {/* Tags by category */}
            {!hasTags && (
              <p style={{ fontSize: '12px', color: '#65625a' }}>
                No tags yet — this image hasn't been through the AI tagging pass.
              </p>
            )}
            {catOrder.map(cat => {
              if (!categories[cat] || categories[cat].length === 0) return null;
              return (
                <div key={cat} style={{ marginBottom: '16px' }}>
                  <div style={{
                    fontSize: '9px', fontWeight: 600, color: '#65625a',
                    letterSpacing: '0.08em', marginBottom: '7px'
                  }}>
                    {(catLabels[cat] || cat).toUpperCase()}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {categories[cat].map(val => (
                      <span key={val} style={{
                        display: 'inline-block',
                        background: 'rgba(201,162,83,0.12)',
                        border: '1px solid rgba(201,162,83,0.25)',
                        borderRadius: '5px',
                        padding: '4px 9px',
                        fontSize: '11.5px', color: '#dcbd76'
                      }}>
                        {val}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}

            {/* Color palette */}
            {image.palette && image.palette.length > 0 && (
              <div style={{ marginBottom: '16px' }}>
                <div style={{
                  fontSize: '9px', fontWeight: 600, color: '#65625a',
                  letterSpacing: '0.08em', marginBottom: '7px'
                }}>
                  COLOR PALETTE
                </div>
                <div style={{ display: 'flex', gap: '4px' }}>
                  {image.palette.map((hex, i) => (
                    <div key={i} title={hex} style={{
                      flex: 1, height: '32px',
                      background: hex, borderRadius: '6px',
                      border: '1px solid rgba(255,255,255,0.08)'
                    }} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer actions */}
        <div style={{
          padding: '12px 20px',
          borderTop: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', gap: '8px', justifyContent: 'flex-end'
        }}>
          <button style={{
            background: 'none', border: '1px solid rgba(201,162,83,0.3)',
            color: '#dcbd76', borderRadius: '6px', padding: '7px 14px',
            cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
          }}>
            ★ Favorite
          </button>
          <button style={{
            background: 'none', border: '1px solid rgba(207,113,82,0.3)',
            color: '#cf7152', borderRadius: '6px', padding: '7px 14px',
            cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
          }}>
            ⚑ Flag
          </button>
        </div>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to   { transform: translateX(0); }
        }
      `}</style>
    </>
  );
}
