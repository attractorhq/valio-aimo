/**
 * Self-contained ISO/IEC 18004 QR Code SVG renderer (Byte mode, ECC Level L, Versions 1–10).
 * Used ONLY in the Approver panel (never on the agent channel, P3).
 */

// Total data codewords and ECC codewords per block for ECC Level L, Versions 1..10.
// Format: [version, totalDataBytes, eccBytesPerBlock, numBlocksGroup1, dataPerBlockG1, numBlocksGroup2, dataPerBlockG2]
const VERSION_TABLE: ReadonlyArray<readonly [number, number, number, number, number, number, number]> = [
  [1, 19, 7, 1, 19, 0, 0],
  [2, 34, 10, 1, 34, 0, 0],
  [3, 55, 15, 1, 55, 0, 0],
  [4, 80, 20, 1, 80, 0, 0],
  [5, 108, 26, 1, 108, 0, 0],
  [6, 136, 18, 2, 68, 0, 0],
  [7, 156, 20, 2, 78, 0, 0],
  [8, 194, 24, 2, 97, 0, 0],
  [9, 232, 30, 2, 116, 0, 0],
  [10, 274, 18, 2, 68, 2, 69],
]

const ALIGNMENT_POSITIONS: ReadonlyArray<readonly number[]> = [
  [],
  [],
  [6, 18],
  [6, 22],
  [6, 26],
  [6, 30],
  [6, 34],
  [6, 22, 38],
  [6, 24, 42],
  [6, 26, 46],
  [6, 28, 50],
]

const GF_EXP = new Uint8Array(512)
const GF_LOG = new Uint8Array(256)
;(() => {
  let x = 1
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x
    GF_LOG[x] = i
    x <<= 1
    if (x & 0x100) x ^= 0x11d
  }
  for (let i = 255; i < 512; i++) {
    GF_EXP[i] = GF_EXP[i - 255]
  }
})()

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0
  return GF_EXP[GF_LOG[a] + GF_LOG[b]]
}

function reedSolomonGenerator(degree: number): Uint8Array {
  let poly = new Uint8Array([1])
  for (let i = 0; i < degree; i++) {
    const next = new Uint8Array(poly.length + 1)
    const root = GF_EXP[i]
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= poly[j]
      next[j + 1] ^= gfMul(poly[j], root)
    }
    poly = next
  }
  return poly
}

function reedSolomonRemainder(data: Uint8Array, degree: number): Uint8Array {
  const gen = reedSolomonGenerator(degree)
  const rem = new Uint8Array(degree)
  for (let i = 0; i < data.length; i++) {
    const factor = data[i] ^ rem[0]
    rem.copyWithin(0, 1)
    rem[degree - 1] = 0
    if (factor !== 0) {
      for (let j = 0; j < degree; j++) {
        rem[j] ^= gfMul(gen[j + 1], factor)
      }
    }
  }
  return rem
}

function encodeCodewords(bytes: Uint8Array) {
  const entry = VERSION_TABLE.find(([ver, cap]) => {
    const lenBits = ver <= 9 ? 8 : 16
    const neededBytes = Math.ceil((4 + lenBits + bytes.length * 8) / 8)
    return neededBytes <= cap
  })
  if (!entry) {
    throw new Error('QR payload exceeds maximum supported length.')
  }
  const [version, totalDataBytes, eccPerBlock, g1Blocks, g1Data, g2Blocks, g2Data] = entry
  const lenBits = version <= 9 ? 8 : 16
  const bits: number[] = []

  const pushBits = (val: number, count: number) => {
    for (let i = count - 1; i >= 0; i--) {
      bits.push((val >>> i) & 1)
    }
  }

  // Byte mode indicator (0100)
  pushBits(0b0100, 4)
  pushBits(bytes.length, lenBits)
  for (const b of bytes) pushBits(b, 8)

  const maxBits = totalDataBytes * 8
  const terminator = Math.min(4, maxBits - bits.length)
  pushBits(0, terminator)
  while (bits.length % 8 !== 0) bits.push(0)

  const dataWords = new Uint8Array(totalDataBytes)
  for (let i = 0; i < bits.length / 8; i++) {
    let byte = 0
    for (let b = 0; b < 8; b++) {
      byte = (byte << 1) | bits[i * 8 + b]
    }
    dataWords[i] = byte
  }
  let padToggle = true
  for (let i = bits.length / 8; i < totalDataBytes; i++) {
    dataWords[i] = padToggle ? 0xec : 0x11
    padToggle = !padToggle
  }

  const dataBlocks: Uint8Array[] = []
  const eccBlocks: Uint8Array[] = []
  let offset = 0
  for (let i = 0; i < g1Blocks; i++) {
    const block = dataWords.subarray(offset, offset + g1Data)
    offset += g1Data
    dataBlocks.push(block)
    eccBlocks.push(reedSolomonRemainder(block, eccPerBlock))
  }
  for (let i = 0; i < g2Blocks; i++) {
    const block = dataWords.subarray(offset, offset + g2Data)
    offset += g2Data
    dataBlocks.push(block)
    eccBlocks.push(reedSolomonRemainder(block, eccPerBlock))
  }

  const interleaved: number[] = []
  const maxDataLen = Math.max(g1Data, g2Data)
  for (let i = 0; i < maxDataLen; i++) {
    for (const block of dataBlocks) {
      if (i < block.length) interleaved.push(block[i])
    }
  }
  for (let i = 0; i < eccPerBlock; i++) {
    for (const block of eccBlocks) {
      interleaved.push(block[i])
    }
  }

  return { version, codewords: interleaved }
}

function formatBits(mask: number): number {
  // ECC Level L = 01
  const data = (0b01 << 3) | mask
  let rem = data << 10
  for (let i = 14; i >= 10; i--) {
    if ((rem >>> i) & 1) rem ^= 0x537 << (i - 10)
  }
  return ((data << 10) | rem) ^ 0x5412
}

function versionBits(version: number): number {
  let rem = version << 12
  for (let i = 17; i >= 12; i--) {
    if ((rem >>> i) & 1) rem ^= 0x1f25 << (i - 12)
  }
  return (version << 12) | rem
}

export function buildQrMatrix(text: string): boolean[][] {
  const bytes = new TextEncoder().encode(text)
  const { version, codewords } = encodeCodewords(bytes)
  const size = version * 4 + 17
  const modules: boolean[][] = Array.from({ length: size }, () => Array<boolean>(size).fill(false))
  const isFunc: boolean[][] = Array.from({ length: size }, () => Array<boolean>(size).fill(false))

  const setFunctionModule = (r: number, c: number, dark: boolean) => {
    if (r >= 0 && r < size && c >= 0 && c < size) {
      modules[r][c] = dark
      isFunc[r][c] = true
    }
  }

  const placeFinder = (row: number, col: number) => {
    for (let dr = -1; dr <= 7; dr++) {
      for (let dc = -1; dc <= 7; dc++) {
        const rr = row + dr
        const cc = col + dc
        const inOuter = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6
        const onBorder = dr === 0 || dr === 6 || dc === 0 || dc === 6
        const inInner = dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4
        setFunctionModule(rr, cc, inOuter && (onBorder || inInner))
      }
    }
  }

  placeFinder(0, 0)
  placeFinder(0, size - 7)
  placeFinder(size - 7, 0)

  for (let i = 8; i < size - 8; i++) {
    setFunctionModule(6, i, i % 2 === 0)
    setFunctionModule(i, 6, i % 2 === 0)
  }

  const alignCoords = ALIGNMENT_POSITIONS[version]
  for (const r of alignCoords) {
    for (const c of alignCoords) {
      if ((r === 6 && c === 6) || (r === 6 && c === size - 7) || (r === size - 7 && c === 6)) {
        continue
      }
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const dist = Math.max(Math.abs(dr), Math.abs(dc))
          setFunctionModule(r + dr, c + dc, dist !== 1)
        }
      }
    }
  }

  // Reserve format information areas and dark module
  for (let i = 0; i <= 8; i++) {
    if (i !== 6) {
      setFunctionModule(8, i, false)
      setFunctionModule(i, 8, false)
    }
  }
  for (let i = 0; i < 8; i++) {
    setFunctionModule(8, size - 1 - i, false)
    setFunctionModule(size - 1 - i, 8, false)
  }
  setFunctionModule(size - 8, 8, true)

  if (version >= 7) {
    const vBits = versionBits(version)
    for (let i = 0; i < 18; i++) {
      const bit = ((vBits >>> i) & 1) !== 0
      const r = Math.floor(i / 3)
      const c = size - 11 + (i % 3)
      setFunctionModule(r, c, bit)
      setFunctionModule(c, r, bit)
    }
  }

  // Place data bits in upward/downward 2-column zig-zag
  let bitIdx = 0
  const totalBits = codewords.length * 8
  let upward = true
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let v = 0; v < size; v++) {
      const row = upward ? size - 1 - v : v
      for (let dc = 0; dc < 2; dc++) {
        const col = right - dc
        if (!isFunc[row][col]) {
          let dark = false
          if (bitIdx < totalBits) {
            const byte = codewords[bitIdx >>> 3]
            dark = ((byte >>> (7 - (bitIdx & 7))) & 1) !== 0
            bitIdx++
          }
          modules[row][col] = dark
        }
      }
    }
    upward = !upward
  }

  // Apply mask 0 ((row + col) % 2 === 0) and write format info
  const mask = 0
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      if (!isFunc[r][c] && (r + c) % 2 === 0) {
        modules[r][c] = !modules[r][c]
      }
    }
  }

  const fBits = formatBits(mask)
  for (let i = 0; i <= 5; i++) modules[8][i] = ((fBits >>> i) & 1) !== 0
  modules[8][7] = ((fBits >>> 6) & 1) !== 0
  modules[8][8] = ((fBits >>> 7) & 1) !== 0
  modules[7][8] = ((fBits >>> 8) & 1) !== 0
  for (let i = 9; i < 15; i++) modules[14 - i][8] = ((fBits >>> i) & 1) !== 0

  for (let i = 0; i < 8; i++) modules[size - 1 - i][8] = ((fBits >>> i) & 1) !== 0
  for (let i = 8; i < 15; i++) modules[8][size - 15 + i] = ((fBits >>> i) & 1) !== 0

  return modules
}

export function QrCode({
  value,
  label = 'Scan on a phone to open the single-use demo approval page',
  size = 148,
}: {
  value: string
  label?: string
  size?: number
}) {
  const matrix = buildQrMatrix(value)
  const quiet = 2
  const dim = matrix.length + quiet * 2
  const pathParts: string[] = []
  for (let r = 0; r < matrix.length; r++) {
    for (let c = 0; c < matrix.length; c++) {
      if (matrix[r][c]) {
        pathParts.push(`M${c + quiet},${r + quiet}h1v1h-1z`)
      }
    }
  }

  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${dim} ${dim}`}
      shapeRendering="crispEdges"
      className="rounded-lg border border-[var(--line)] bg-white p-1"
    >
      <rect width={dim} height={dim} fill="#ffffff" />
      <path d={pathParts.join('')} fill="#15302a" />
    </svg>
  )
}
