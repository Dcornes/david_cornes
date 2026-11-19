import { runTable } from './heatmap-page';
import { heatBin } from './run-heatmap';

describe('heatmap', () => {
  it('bins counts light to dark', () => {
    expect(heatBin(0)).toBeNull();
    expect(heatBin(1)?.label).toBe('1');
    expect(heatBin(3)?.label).toBe('2–3');
    expect(heatBin(40)?.label).toBe('16+');
  });

  it('lists named runs by their busiest piece, most skied first', () => {
    const run = (id: string, name: string) => ({ id, name, difficulty: 'expert', area: false, points: [] });
    const runs = [run('1', 'Liberty Bowl'), run('2', 'Liberty Bowl'), run('3', 'Lobo'), run('4', 'Unnamed run'), run('5', 'Ambush')];
    const rows = runTable(runs, { '1': 2, '2': 5, '3': 3, '4': 9 });
    expect(rows.map((r) => [r.name, r.times])).toEqual([
      ['Liberty Bowl', 5],
      ['Lobo', 3],
    ]);
  });
});

describe('heatLines', () => {
  it('joins neighbouring cells once each, coloured by the hotter cell', async () => {
    const { heatLines } = await import('./run-heatmap');
    // An L of three cells, plus one cell off on its own.
    const lines = heatLines({ cell_m: 10, center: [45, -111], cells: [[0, 0, 1], [1, 0, 1], [1, 1, 9], [5, 5, 1]] });
    const segments = lines.flatMap((f) => f.geometry.coordinates);
    expect(segments.length).toBe(4); // (0,0)-(1,0), (0,0)-(1,1), (1,0)-(1,1), and the lone cell's dot
    const hottest = lines[lines.length - 1];
    expect(hottest.geometry.coordinates.length).toBe(2); // both segments touching the busy cell
  });
});
