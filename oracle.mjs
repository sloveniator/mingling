// oracle.mjs — 用 iztro 生成暗盘准确性压力测试的真值 + 后端形态 fixture
// 输出 oracle_fixtures.json
import { astro } from 'iztro';
import { writeFileSync } from 'fs';

// 时辰映射：hour(0-23) -> iztro timeIndex (0=早子时 .. 11=亥时, 12=晚子时)
function hourToTimeIndex(h) {
  if (h === 23) return 12;          // 晚子时
  return Math.floor((h + 1) / 2);  // 0->0,1->1,2->1,...22->11
}

const CUR = new Date().getFullYear();

// 出生样本：覆盖 1955-2010、男女、各月日时（含闰月附近/边界）
const births = [
  [1990, 6, 15, 10, 'male'],
  [1985, 12, 30, 22, 'female'],
  [2000, 2, 5, 3, 'male'],
  [1975, 8, 20, 14, 'female'],
  [1965, 1, 1, 0, 'male'],
  [2005, 7, 7, 12, 'female'],
  [1958, 3, 18, 8, 'male'],
  [1992, 11, 11, 23, 'female'], // 晚子时
  [1980, 5, 1, 6, 'male'],
  [1970, 9, 9, 18, 'female'],
  [2010, 4, 4, 4, 'male'],
  [1960, 10, 10, 20, 'female'],
  [1995, 8, 8, 16, 'male'],
  [1988, 2, 29, 2, 'female'], // 闰年前
  [1978, 6, 6, 10, 'male'],
  [2002, 12, 25, 0, 'female'],
  [1968, 7, 15, 13, 'male'],
  [1999, 3, 3, 9, 'female'],
  [1983, 4, 4, 15, 'male'],
  [1955, 9, 9, 21, 'female'],
  [2008, 1, 20, 7, 'male'],
  [1972, 11, 30, 19, 'female'],
  [1994, 5, 5, 11, 'male'],
  [1986, 2, 14, 1, 'female'],
  [1962, 6, 23, 17, 'male'],
  [2003, 9, 18, 5, 'female'],
  [1976, 12, 12, 23, 'male'],
  [1991, 7, 1, 14, 'female'],
  [1989, 10, 1, 8, 'male'],
  [1966, 3, 3, 2, 'female'],
  [2006, 8, 8, 20, 'male'],
  [1973, 4, 15, 12, 'female'],
  [1997, 11, 22, 22, 'male'],
  [1981, 1, 8, 6, 'female'],
  [1959, 5, 5, 16, 'male'],
  [2001, 6, 30, 10, 'female'],
];

function buildDecadals(a) {
  // 枚举全部大运：在每段大运中点调用 horoscope 收集 decadal
  const seen = new Map();
  const base = a.solarDate; // 'YYYY-M-D'
  const by = parseInt(base.split('-')[0]);
  for (let off = -2; off <= 120; off += 10) {
    const ty = by + 7 + off; // 大运约从虚岁7起
    if (ty < by || ty > CUR + 5) continue;
    const h = a.horoscope(`${ty}-6-15`);
    const d = h.decadal;
    const key = `${d.heavenlyStem}${d.earthlyBranch}`;
    if (!seen.has(key)) seen.set(key, { ganzhi: key, range: d.range });
  }
  // 按起始年龄排序
  return [...seen.values()].sort((x, y) => x.range[0] - y.range[0]);
}

const out = [];
for (const [y, m, d, h, g] of births) {
  const gender = g === 'male' ? '男' : '女';
  const a = astro.bySolar(`${y}-${m}-${d}`, hourToTimeIndex(h), gender);
  const hor = a.horoscope();
  const dy = hor.decadal;
  const ln = hor.yearly;
  const truth_da_yun = `${dy.heavenlyStem}${dy.earthlyBranch}`;
  const truth_liu_nian = `${ln.heavenlyStem}${ln.earthlyBranch}`;
  const nominalAge = hor.age.nominalAge;          // 虚岁
  const pluginAge = CUR - y;                       // 周岁/年差

  const decadals = buildDecadals(a);
  // 命理正确的大运序列（虚岁 range）
  const daYun_virtual = decadals.map((x) => ({
    ganzhi: x.ganzhi, startAge: x.range[0], endAge: x.range[1],
  }));

  // 构造后端形态 fixture（与当前后端一致：用 虚岁 range 填充，插件按周岁匹配）
  const daYun_fixture = decadals.map((x, i) => ({
    ganZhi: x.ganzhi,
    startAge: x.range[0],
    endAge: x.range[1],
    isTongXian: false,
    liuNian: [],
  }));
  // 童限占位（起运前）：第一个真实大运之前
  const firstStart = daYun_fixture.length ? daYun_fixture[0].startAge : 7;
  daYun_fixture.unshift({
    ganZhi: '', startAge: 0, endAge: Math.max(0, firstStart - 1),
    isTongXian: true, liuNian: [],
  });
  // 给当前大运补 liuNian（含本年）
  for (const e of daYun_fixture) {
    if (e.ganZhi === truth_da_yun) {
      e.liuNian = [{ year: CUR, ganZhi: truth_liu_nian }];
    }
  }

  // 紫微宫位 fixture：name / isMingGong / major stars
  const palaces = a.palaces.map((p) => {
    const majors = (p.stars || [])
      .filter((s) => s.type === 'major')
      .map((s) => s.name);
    return {
      name: p.name,
      isMingGong: !!p.isMainPalace,
      majorStars: majors,
    };
  });
  const mingGong = palaces.find((p) => p.isMingGong || p.name === '命宫');
  const mingStars = mingGong ? mingGong.majorStars : [];

  out.push({
    birth: { year: y, month: m, day: d, hour: h, gender: g },
    currentYear: CUR,
    nominalAge, pluginAge,
    truth_da_yun, truth_liu_nian,
    daYun_virtual,
    daYun_fixture,
    ziwei: {
      palaces: palaces.map((p) => ({
        name: p.name,
        isMingGong: p.isMingGong,
        stars: p.majorStars.map((n) => ({ name: n, type: 'major' })),
      })),
      mingStars,
    },
  });
}

writeFileSync('oracle_fixtures.json', JSON.stringify(out, null, 2));
console.log(`wrote ${out.length} fixtures; sample:`);
console.log(JSON.stringify(out[0], null, 2).slice(0, 900));
