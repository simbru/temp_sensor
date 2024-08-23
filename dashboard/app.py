from shiny.express import input, render, ui
from shinywidgets import render_altair

ui.input_selectize(
    "var", "Select variable",
    choices=["bill_length_mm", "body_mass_g"]
)

@render.plot
def hist():
    from matplotlib import pyplot as plt
    import seaborn as sns

    df = sns.load_dataset('penguins')
    df[input.var()].hist(grid=False)
    plt.xlabel(input.var())
    plt.ylabel("count")

@render_altair
def histalt():
    import altair as alt
    import seaborn as sns
    df = sns.load_dataset('penguins')
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(x=alt.X(f"{input.var()}:Q", bin=True), y="count()")
    ).interactive()