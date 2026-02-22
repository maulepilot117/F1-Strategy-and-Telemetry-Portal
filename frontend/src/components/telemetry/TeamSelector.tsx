/**
 * TeamSelector — dropdown to pick which team's two drivers to display.
 *
 * Uses the LiveTeam[] data from the backend API (fetched before going live).
 * Each team has a colour swatch for visual identification.
 */

import type { LiveTeam } from "../../types";

interface TeamSelectorProps {
  teams: LiveTeam[];
  selectedTeam: string;
  onChange: (team: string) => void;
}

export default function TeamSelector({ teams, selectedTeam, onChange }: TeamSelectorProps) {
  const currentTeam = teams.find((t) => t.team === selectedTeam);

  return (
    <div className="flex items-center gap-2">
      {/* Team colour swatch */}
      {currentTeam?.team_color && (
        <div
          className="w-3 h-3 rounded-full flex-shrink-0"
          style={{ backgroundColor: currentTeam.team_color }}
        />
      )}
      <select
        value={selectedTeam}
        onChange={(e) => onChange(e.target.value)}
        className="bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-2 py-1 focus:outline-none focus:border-f1-red"
      >
        {teams.length === 0 && <option value="">Select GP first</option>}
        {teams.map((t) => (
          <option key={t.team} value={t.team}>
            {t.team}
          </option>
        ))}
      </select>
    </div>
  );
}
