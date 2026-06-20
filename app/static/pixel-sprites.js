// ============================================================
// pixel-sprites.js - 像素精灵工厂
// 用离屏 Canvas 生成 16x16 精致像素角色纹理
// ============================================================

// 16x16 像素角色模板（正面站立）
// 字符 -> 颜色映射由调色板提供
const SPRITE_FRONT = [
  "................",
  "....HHHHHHHH....",
  "...HhhhhhhhhH...",
  "..HhhsssssshhH..",
  "..Hhssssssss hH.",
  "..Hhs s s s  h..",
  "..Hhsseessees h.",  // 眼睛
  "..Hhssssssss h..",
  "..Hhss mm s  h..",  // 鼻+嘴
  "..Hhhssssss hH..",
  "...hsssssssh....",
  "....cCcccccCc...",  // 衣领
  "...cCBbbbbbBCc..",  // 肩膀+上衣
  "..cCbbbbbbbbBCc.",
  "..cBbbblbbbblbc.",  // 纽扣
  "..cBbbblbbbblbc.",
  "...pppppppppp...",  // 裤子
  "...pppp..ppppp..",
  "...xxx...xxxx...",  // 鞋子
];

// 行走帧（脚部交替）
const SPRITE_WALK1 = [
  "................",
  "....HHHHHHHH....",
  "...HhhhhhhhhH...",
  "..HhhsssssshhH..",
  "..Hhssssssss hH.",
  "..Hhs s s s  h..",
  "..Hhsseessees h.",
  "..Hhssssssss h..",
  "..Hhss mm s  h..",
  "..Hhhssssss hH..",
  "...hsssssssh....",
  "....cCcccccCc...",
  "...cCBbbbbbBCc..",
  "..cCbbbbbbbbBCc.",
  "..cBbbblbbbblbc.",
  "..cBbbblbbbblbc.",
  "...pppppppppp...",
  "...ppp....pppp..",  // 左脚前
  "...xxx....xxx...",
];

const SPRITE_WALK2 = [
  "................",
  "....HHHHHHHH....",
  "...HhhhhhhhhH...",
  "..HhhsssssshhH..",
  "..Hhssssssss hH.",
  "..Hhs s s s  h..",
  "..Hhsseessees h.",
  "..Hhssssssss h..",
  "..Hhss mm s  h..",
  "..Hhhssssss hH..",
  "...hsssssssh....",
  "....cCcccccCc...",
  "...cCBbbbbbBCc..",
  "..cCbbbbbbbbBCc.",
  "..cBbbblbbbblbc.",
  "..cBbbblbbbblbc.",
  "...pppppppppp...",
  "...ppppp...ppp..",  // 右脚前
  "...xxxx....xxx..",
];

// 角色调色板（每种角色类型一套精致配色）
const PALETTES = {
  customer: {
    H: 0x3D2817,  // 头发深棕
    h: 0x5C3A22,  // 头发亮棕
    s: 0xFFD9B3,  // 皮肤亮
    S: 0xE8B894,  // 皮肤暗（阴影）
    e: 0x2A1A0A,  // 眼睛黑
    m: 0xC44545,  // 嘴红
    c: 0x4A90D9,  // 衣服主蓝
    C: 0x3A7ABF,  // 衣服阴影
    b: 0x6BA8E8,  // 衣服亮色
    B: 0x2E6BA8,  // 衣服深色边
    l: 0xFFD93D,  // 纽扣金
    p: 0x2C3E50,  // 裤子深蓝
    x: 0x1A1A1A,  // 鞋子黑
  },
  waiter: {
    H: 0x1A1A1A,  // 黑发
    h: 0x333333,  // 黑发亮
    s: 0xFFD9B3,
    S: 0xE8B894,
    e: 0x2A1A0A,
    m: 0xC44545,
    c: 0xF4C430,  // 围裙黄
    C: 0xD4A017,  // 围裙深
    b: 0xFFD93D,  // 围裙亮
    B: 0xB8860B,  // 围裙边
    l: 0xE8E8E8,  // 白纽扣
    p: 0x2A2A2A,  // 黑裤
    x: 0x0A0A0A,
  },
  barista: {
    H: 0x4A2F1A,  // 棕发
    h: 0x6B4423,
    s: 0xFFD9B3,
    S: 0xE8B894,
    e: 0x2A1A0A,
    m: 0xC44545,
    c: 0xE8743C,  // 制服橙
    C: 0xC85A2A,  // 制服深
    b: 0xFF8A56,  // 制服亮
    B: 0xA04A20,  // 制服边
    l: 0xFFD93D,  // 金扣
    p: 0x3D2817,  // 棕裤
    x: 0x2A1810,
  },
  cashier: {
    H: 0x6B3FA0,  // 紫发
    h: 0x8B5FBF,
    s: 0xFFD9B3,
    S: 0xE8B894,
    e: 0x2A1A0A,
    m: 0xC44545,
    c: 0x81C784,  // 绿衣
    C: 0x66BB6A,
    b: 0xA5D6A7,
    B: 0x4CAF50,
    l: 0xFFD93D,
    p: 0x2E7D32,
    x: 0x1B5E20,
  },
};

// 将字符模板渲染到 canvas
function renderSpriteToCanvas(template, palette, pixelSize = 2) {
  const width = 16;
  const height = template.length;
  const canvas = document.createElement('canvas');
  canvas.width = width * pixelSize;
  canvas.height = height * pixelSize;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  for (let y = 0; y < template.length; y++) {
    const row = template[y];
    for (let x = 0; x < row.length && x < width; x++) {
      const ch = row[x];
      if (ch === '.' || ch === ' ') continue;
      const color = palette[ch];
      if (color !== undefined) {
        ctx.fillStyle = '#' + color.toString(16).padStart(6, '0');
        ctx.fillRect(x * pixelSize, y * pixelSize, pixelSize, pixelSize);
      }
    }
  }
  return canvas;
}

// 为场景注册所有角色纹理（3帧：站/走1/走2）
function registerCharacterTextures(scene) {
  const types = ['customer', 'waiter', 'barista', 'cashier'];
  const frames = [
    { key: 'front', tpl: SPRITE_FRONT },
    { key: 'walk1', tpl: SPRITE_WALK1 },
    { key: 'walk2', tpl: SPRITE_WALK2 },
  ];

  for (const type of types) {
    const palette = PALETTES[type];
    for (const frame of frames) {
      const texKey = `char_${type}_${frame.key}`;
      if (scene.textures.exists(texKey)) continue;
      const canvas = renderSpriteToCanvas(frame.tpl, palette, 2);
      scene.textures.addCanvas(texKey, canvas);
    }
  }
}

// 导出给全局（非模块化加载）
window.PixelSprites = {
  SPRITE_FRONT,
  SPRITE_WALK1,
  SPRITE_WALK2,
  PALETTES,
  renderSpriteToCanvas,
  registerCharacterTextures,
};
