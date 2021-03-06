import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

X = np.linspace(0, 127, 128)
Y = np.linspace(0, 127, 128)
data = pd.read_csv("Vorticity_spinn_128.csv")
def f(X,Y):
    return (data.iloc[X,Y])

fig = plt.figure()
plt.title('128 * 128 contour of Vorticity from SpiNNaker')
ax = plt.subplot(1,1,1)
plt.contour(X, Y, f(X,Y), 32, colors='k', linewidths=(0.7,))
plt.contourf(X, Y, f(X,Y), 32, alpha=0.25, colors='black', linewidth=(0.7))
ax.set_aspect('equal')
plt.tight_layout()
plt.show()
