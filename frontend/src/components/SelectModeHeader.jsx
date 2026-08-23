import { useIsMobile } from '../hooks/useIsMobile';
import { useAuth } from '../AuthContext';
import { error, onSurfaceVariant, primary, surfaceContainerLow, tertiary } from '../theme';

export default function SelectModeHeader({
  selectedIds,
  setSelectedIds,
  onSelectAllResults,
  onExit,
  onEditTags,
  onCrop,
  onDelete,
  selectingAll,
  selectMsg,
  totalResults = 0,
  images = [],
  everythingLoaded,
  allLoadedAndSelected,
}) {
  const isMobile = useIsMobile();
  const { isAdmin } = useAuth();
  const count = selectedIds.size;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      padding: isMobile ? '12px 14px' : '12px 20px',
      background: surfaceContainerLow,
      borderBottom: '1px solid rgba(255,255,255,0.065)',
      flexWrap: 'wrap',
      rowGap: '8px'
    }}>
      {/* Selection count badge */}
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        background: 'rgba(184,206,161,0.14)',
        border: '1px solid rgba(184,206,161,0.5)',
        borderRadius: '99px',
        padding: '5px 12px',
        fontSize: '12.5px',
        color: tertiary,
        fontWeight: 500,
        flexShrink: 0
      }}>
        {count} selected
      </span>

      {/* Select all */}
      <button
        onClick={onSelectAllResults}
        disabled={selectingAll || allLoadedAndSelected}
        title={everythingLoaded
          ? 'Select every image in these results'
          : 'Select every image these filters match — including the ones further down that haven\'t loaded yet'}
        style={{
          background: 'none',
          border: '1px solid rgba(255,255,255,0.12)',
          color: onSurfaceVariant,
          borderRadius: '8px',
          padding: '7px 12px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'inherit',
          opacity: selectingAll ? 0.6 : 1,
          flexShrink: 0
        }}
      >
        {selectingAll ? 'Selecting…' : `Select all ${totalResults || images.length}`}
      </button>

      {/* Clear selection */}
      <button
        onClick={() => setSelectedIds(new Set())}
        style={{
          background: 'none',
          border: '1px solid rgba(255,255,255,0.12)',
          color: onSurfaceVariant,
          borderRadius: '8px',
          padding: '7px 12px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'inherit',
          flexShrink: 0
        }}
      >
        Clear selection
      </button>

      {selectMsg && (
        <span style={{
          fontSize: '11.5px',
          color: error,
          flexShrink: 0
        }}>
          {selectMsg}
        </span>
      )}

      {/* Crop button */}
      {count > 0 && onCrop && (
        <button
          onClick={onCrop}
          title="Auto-detect and remove letterbox bars / screenshot chrome from the selected images"
          style={{
            background: 'rgba(217,164,65,0.14)',
            border: '1px solid rgba(217,164,65,0.5)',
            color: primary,
            borderRadius: '8px',
            padding: '7px 14px',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
            fontFamily: 'inherit',
            flexShrink: 0
          }}
        >
          ✂ Crop {count}
        </button>
      )}

      {/* Delete button */}
      {count > 0 && (
        <button
          onClick={onDelete}
          title="Move the selected photos to Drive's _Removed folder and remove them from Frame Atlas"
          style={{
            background: 'rgba(255,180,171,0.14)',
            border: '1px solid rgba(255,180,171,0.5)',
            color: error,
            borderRadius: '8px',
            padding: '7px 14px',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
            fontFamily: 'inherit',
            flexShrink: 0
          }}
        >
          🗑 Delete {count}
        </button>
      )}

      <div style={{ flex: 1 }} />

      {/* Edit tags button (opens drawer) */}
      {count > 0 && isAdmin && (
        <button
          onClick={onEditTags}
          title="Edit tags and filmography for the selected images"
          style={{
            background: 'rgba(201,162,83,0.14)',
            border: '1px solid rgba(201,162,83,0.5)',
            color: primary,
            borderRadius: '8px',
            padding: '7px 14px',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
            fontFamily: 'inherit',
            flexShrink: 0
          }}
        >
          Edit tags
        </button>
      )}

      {/* Exit button */}
      <button
        onClick={onExit}
        style={{
          background: 'none',
          border: '1px solid rgba(255,180,171,0.35)',
          color: error,
          borderRadius: '8px',
          padding: '7px 12px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'inherit',
          flexShrink: 0
        }}
      >
        Exit
      </button>
    </div>
  );
}
