import { setRequestLocale } from "next-intl/server";
import { Suspense, use } from "react";

import { RecipePage } from "@/components/RecipePage";

type Props = {
  params: Promise<{ locale: string; id: string }>;
};

export default function RecipeRoute({ params }: Props) {
  const { locale, id } = use(params);
  setRequestLocale(locale);

  return (
    <Suspense>
      <RecipePage id={Number(id)} />
    </Suspense>
  );
}
