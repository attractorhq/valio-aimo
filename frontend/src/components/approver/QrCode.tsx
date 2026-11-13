import QRCode from 'qrcode'

/**
 * ISO/IEC 18004 QR Code SVG renderer backed by `qrcode` (ECC Level M, 4-module quiet zone).
 * Used ONLY in the Approver panel (never on the agent channel, P3).
 */
export function buildQrMatrix(text: string): boolean[][] {
  const qr = QRCode.create(text, { errorCorrectionLevel: 'M' })
  const size = qr.modules.size
  const data = qr.modules.data
  const matrix: boolean[][] = []
  for (let r = 0; r < size; r++) {
    const row: boolean[] = []
    for (let c = 0; c < size; c++) {
      row.push(Boolean(data[r * size + c]))
    }
    matrix.push(row)
  }
  return matrix
}

export function QrCode({
  value,
  label = 'Scan on a phone to open the single-use demo approval page',
  size = 160,
}: {
  value: string
  label?: string
  size?: number
}) {
  const matrix = buildQrMatrix(value)
  const quiet = 4
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
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={label}
      data-qr-payload={value}
      width={size}
      height={size}
      viewBox={`0 0 ${dim} ${dim}`}
      shapeRendering="crispEdges"
      className="rounded-lg border border-[var(--line)] bg-white p-1"
    >
      <rect width={dim} height={dim} fill="#ffffff" />
      <path d={pathParts.join('')} fill="#000000" />
    </svg>
  )
}
