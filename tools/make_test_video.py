"""生成用于测试的示例视频（多个明显不同的场景），仅开发调试用。"""
import numpy as np, cv2, sys
out = sys.argv[1] if len(sys.argv) > 1 else '/tmp/test_input.mp4'
fps, w, h = 25, 640, 360
scenes = [(3.0, (240, 30, 30)), (2.5, (30, 240, 30)), (4.0, (30, 30, 240)),
          (2.0, (240, 240, 30)), (5.5, (240, 30, 240))]
vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
for dur, color in scenes:
    for i in range(int(dur * fps)):
        img = np.full((h, w, 3), color, np.uint8)
        cv2.putText(img, f'{i}', (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
        vw.write(img)
vw.release()
print('written', out)
