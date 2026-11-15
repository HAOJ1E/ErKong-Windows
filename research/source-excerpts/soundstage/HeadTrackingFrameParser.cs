using System.Buffers.Binary;

namespace SoundStage.Core;

public readonly record struct HeadTrackingFrame(
    float Pitch,
    float Yaw,
    short HorizontalAcceleration,
    short VerticalAcceleration);

public sealed class HeadTrackingFrameParser
{
    private const int CalibrationSampleCount = 10;
    private readonly List<(short O1, short O2, short O3)> _calibration = [];
    private float _neutralO1;
    private float _neutralO2;
    private float _neutralO3;

    public bool IsCalibrated { get; private set; }

    public bool TryParse(
        ReadOnlySpan<byte> packet,
        out HeadTrackingFrame frame)
    {
        frame = default;
        if (packet.Length <= 60 ||
            !packet[..10].SequenceEqual(
                new byte[]
                {
                    0x04, 0x00, 0x04, 0x00, 0x17,
                    0x00, 0x00, 0x00, 0x10, 0x00,
                }) ||
            (packet[10] != 0x44 && packet[10] != 0x45) ||
            packet[11] != 0x00)
        {
            return false;
        }

        var o1 = BinaryPrimitives.ReadInt16LittleEndian(packet[43..45]);
        var o2 = BinaryPrimitives.ReadInt16LittleEndian(packet[45..47]);
        var o3 = BinaryPrimitives.ReadInt16LittleEndian(packet[47..49]);
        var horizontal =
            BinaryPrimitives.ReadInt16LittleEndian(packet[51..53]);
        var vertical =
            BinaryPrimitives.ReadInt16LittleEndian(packet[53..55]);

        if (!IsCalibrated)
        {
            _calibration.Add((o1, o2, o3));
            if (_calibration.Count < CalibrationSampleCount)
            {
                return false;
            }

            _neutralO1 = (float)_calibration.Average(sample => sample.O1);
            _neutralO2 = (float)_calibration.Average(sample => sample.O2);
            _neutralO3 = (float)_calibration.Average(sample => sample.O3);
            IsCalibrated = true;
        }

        var normalizedO1 = o1 - _neutralO1;
        var normalizedO2 = o2 - _neutralO2;
        var normalizedO3 = o3 - _neutralO3;
        _ = normalizedO1;
        var pitch = (normalizedO2 + normalizedO3) * 0.5f / 32000f * 180f;
        var yaw = (normalizedO2 - normalizedO3) * 0.5f / 32000f * 180f;
        frame = new HeadTrackingFrame(pitch, yaw, horizontal, vertical);
        return true;
    }

    public void Reset()
    {
        _calibration.Clear();
        IsCalibrated = false;
        _neutralO1 = 0;
        _neutralO2 = 0;
        _neutralO3 = 0;
    }
}
